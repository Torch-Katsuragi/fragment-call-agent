# -*- coding: utf-8 -*-
"""テスト用の相手役エージェント (caller-sim) — ダミー番号の「発信者」を演じる。

一人二役のテストから解放されるための道具。SIPを通さず、LiveKitルーム内で
電話アシスタントと直接会話する。管制室からは実着信と同じに見える
(文字起こし・吹き出し・耳打ち・交代すべて使える)。

- 明示ディスパッチ専用 (agent_name="caller-sim"): 実着信のルームには絶対に入らない
- シナリオは dispatch の metadata で渡す (管制室の「テスト通話」ボタン → /api/testcall)
- ルーム名は call_<ダミー番号>_sim<乱数> — 電話アシスタント側はルーム名から番号を拾う
"""

import asyncio
import logging
import os

from google.genai import types as genai_types
from livekit import api as lk_api
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentSession,
    AutoSubscribe,
    JobContext,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
)
from livekit.agents.voice.room_io import RoomInputOptions
from livekit.plugins import google, silero

logger = logging.getLogger("caller-sim")

# 本人の呼び名 (相手役のセリフ用)。⚠氏名をソースに書かない
_OWNER = os.environ.get("OWNER_NAME", "").strip() or "この番号の持ち主"

DEFAULT_SCENARIO = (
    f"あなたは{_OWNER}の友人「佐藤」。近いうちに食事に行きたくて電話をかけた。"
    "日程はまだ決めていない。折り返しの約束が取れれば満足。"
)


def sim_instructions(scenario: str) -> str:
    return f"""あなたは電話応対のテストで「電話をかけてきた相手」を演じる役者です。

# シナリオ
{scenario}

# 演技のルール
- あなたは発信者。相手 (自動応答AIまたは本人) が先に出て挨拶するので、
  それを聞いてから名乗り、用件を話し始める
- 電話らしく1〜2文ずつ短く話す。一度に全部説明しない
- 相手の質問には自然に答える。シナリオにない細部 (日時の希望など) は自分で作ってよい
- 用件が伝わって会話が終わったら、別れの挨拶をして end_call ツールで通話を切る
- 自分がAIであることやテストであることは明かさない。演技を貫く
"""


class SimCaller(Agent):
    def __init__(self, scenario: str, job_ctx: JobContext):
        super().__init__(instructions=sim_instructions(scenario))
        self._job_ctx = job_ctx

    @function_tool
    async def end_call(self, context: RunContext) -> None:
        """用件が済んで会話を終える時に通話を切る。別れの挨拶と同じターンで呼んでよい。"""
        logger.info("caller-sim: end_call")
        try:
            await context.wait_for_playout()
        except Exception:
            pass
        await self._job_ctx.api.room.delete_room(
            lk_api.DeleteRoomRequest(room=self._job_ctx.room.name)
        )


def _make_session() -> AgentSession:
    creds = os.environ.get("GOOGLE_TTS_CREDENTIALS", "/secrets/callagent-tts.json")
    return AgentSession(
        vad=silero.VAD.load(),
        stt=google.STT(
            languages="ja-JP",
            detect_language=False,
            model="latest_long",
            credentials_file=creds,
        ),
        # 相手役は少し高めのtemperatureで人間らしい揺らぎを出す
        # (モデルはアシスタント本体と揃える。3.5→3.6は2026-08-01)
        llm=google.LLM(
            model="gemini-3.6-flash",
            temperature=0.9,
            thinking_config=genai_types.ThinkingConfig(thinking_level="low"),
        ),
        # 声はアシスタント (Leda) と混ざらないよう男性系をデフォルトに
        tts=google.TTS(
            language="ja-JP",
            voice_name=os.environ.get("SIM_VOICE", "ja-JP-Chirp3-HD-Charon"),
            use_streaming=True,
            credentials_file=creds,
        ),
    )


async def entrypoint(ctx: JobContext):
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    scenario = (ctx.job.metadata or "").strip() or DEFAULT_SCENARIO
    logger.info("caller-sim joined %s / scenario: %s", ctx.room.name, scenario[:80])

    # 電話アシスタント (AGENT種別) の入室を待ってから会話を始める
    peer = await ctx.wait_for_participant(kind=rtc.ParticipantKind.PARTICIPANT_KIND_AGENT)

    session = _make_session()
    heard = {"any": False}

    @session.on("conversation_item_added")
    def _on_item(ev):
        heard["any"] = True

    # 通常は電話アシスタントを聴く。operator (本人) が出たら聴く相手を人間に切り替える —
    # 単一参加者リンクのままだと交代/受話の瞬間に相手役が誰の声も聴かず沈黙する (2026-07-18実測)
    await session.start(
        room=ctx.room,
        agent=SimCaller(scenario, ctx),
        room_input_options=RoomInputOptions(
            participant_identity=peer.identity,
            participant_kinds=[
                rtc.ParticipantKind.PARTICIPANT_KIND_AGENT,
                rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD,
            ],
        ),
    )

    def _listen_to(identity: str) -> None:
        try:
            session.room_io.set_participant(identity)
            logger.info("caller-sim: 聴く相手を切替 → %s", identity)
        except Exception:
            logger.exception("聴く相手の切替に失敗")

    @ctx.room.on("participant_connected")
    def on_participant_connected(p: rtc.RemoteParticipant):
        if p.identity.startswith("operator-"):
            _listen_to(p.identity)  # 本人が出た/交代した → 人間を聴く

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(p: rtc.RemoteParticipant):
        if p.identity.startswith("operator-"):
            still = any(
                pp.identity.startswith("operator-")
                for pp in ctx.room.remote_participants.values()
            )
            if not still:
                _listen_to(peer.identity)  # AIに任せるに戻った → AIを聴く

    # 受話フローでは operator が先に入室済みのことがある
    for p in ctx.room.remote_participants.values():
        if p.identity.startswith("operator-"):
            _listen_to(p.identity)
            break

    # 誰も話さないままなら自分から切り出す (書記モードの受話は挨拶が無いので、
    # 発信者らしく「もしもし？」と呼びかける)
    async def nudge():
        await asyncio.sleep(7)
        if not heard["any"]:
            logger.info("caller-sim: 無言が続くのでこちらから話しかける")
            session.generate_reply(
                instructions="電話がつながったのに相手が何も言いません。"
                "発信者らしく「もしもし？」と短く呼びかけてください。"
            )

    asyncio.create_task(nudge())


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="caller-sim"))
