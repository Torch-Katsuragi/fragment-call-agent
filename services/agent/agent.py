"""Call-Agent Voice Agent — プロダクト名「フラグメント」.

M2:   AIコアは AI_CORE 環境変数で切替 (_make_session 参照)。DESIGN.md §5.3。
      - live (デフォルト): Gemini Live API (3.1-flash-live-preview、音声→音声)。応答が速く
        ターン交代が自然。初期の「頭悪い」印象はプロンプト改善 (正直な状況設定+番号注入) で
        大幅に緩和されたため、レイテンシ最優先でこちらをデフォルトに戻した (2026-07-16)
      - cascade: Deepgram STT → Gemini 3.5 Flash (テキスト・thinking無効) → TTS(_make_tts)。
        賢さは上だが実測で応答が壊滅的に遅かった (thinking + 非ストリーミングTTSの積み上げ)。
        thinking無効化を入れたので、賢さが必要になったら再評価する
M2.5: 通話履歴のDB永続化 (PostgreSQL: calls + transcript_segments、infra/postgres/init.sql)。
      search_past_calls ツールはDBを検索する。JSONLは生ログのバックアップとして継続。
      発信者番号はプロンプトに注入する (システムが取得済みのものを口頭で聞き直さない)。

- 文字起こしは AgentSession が自動で lk.transcription テキストストリームに配信する
  (interim→final、話者=送信者identity)。Web UI 側は useTranscriptions で購読するだけ

必要な環境変数: GOOGLE_API_KEY / DEEPGRAM_API_KEY / ELEVEN_API_KEY / DATABASE_URL
    (GOOGLE_API_KEYの発行はブラウザ自動化だと不正利用検知に弾かれるため手動で — 2026-07-16実測)
    (ELEVEN_API_KEY はプラグインの規定名 — ELEVENLABS_API_KEY ではない)

M1実測の注意 (再発防止):
- auto_subscribe は bool ではなく AutoSubscribe 列挙型 (True だと購読されず無音になる)
- ctx.create_task は存在しない → asyncio.create_task
"""

import asyncio
import json
import logging
import os
import re
import urllib.parse
from datetime import datetime, timedelta, timezone

from livekit import rtc
from livekit.agents import (
    Agent,
    AudioConfig,
    AutoSubscribe,
    BackgroundAudioPlayer,
    ConversationItemAddedEvent,
    JobContext,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
)
from livekit.agents import metrics as lk_metrics
from livekit.agents.llm import ChatMessage
from livekit.agents.voice.room_io import RoomInputOptions

from call_db import CallDb, TranscriptWriter, _safe_delete_room, safe_delete_room
from http_server import HttpCallCtx, run_http_server
from media_pipeline import (
    CONNECT_GRACE_SEC,
    HOLD_MUSIC,
    HOLD_TIMEOUT_LINE,
    HOLD_TIMEOUT_SEC,
    LEAVE_SETTLE_SEC,
    SILENCE_SEC,
    TAKEOVER_LINE,
    TTS_CHOICES,
    _hold_music_loop,
    _load_hold_music,
    _make_google_tts,
    _make_live_vad,
    _make_one_tts,
    _make_session,
    _make_stt,
    _make_tts,
)
from stt_refine import (
    CallSttTracker,
    _get_refine_client,
    _pcm16k_to_wav,
    _refine_transcript_with_audio,
    transcribe_operator_track,
    watch_caller_track,
)
from workspace_config import (
    PROMPT_SECTIONS,
    PROMPTS_DIR,
    load_prompt_section,
    load_workspace_prompts,
    strip_frontmatter,
    urgent_allowed,
)
from workspace_config import urgent_allowed as _urgent_allowed

logger = logging.getLogger("call-agent")

# ユーザー編集可能なシステムプロンプトは M4 以降 (DESIGN §5.3)。M2 は固定文で検証する。
JST = timezone(timedelta(hours=9))


def _now_jst_str() -> str:
    now = datetime.now(JST)
    return f"{now:%Y-%m-%d}({'月火水木金土日'[now.weekday()]}) {now:%H:%M}"


# 方針: モデルに自分の状況を正直に伝える (2026-07-16 実機テストで「存在しない取り次ぎを
# 約束して黙り込む」症状 → 状況を隠さず説明する方が誤った約束をしなくなる)
OWNER_NAME = os.environ.get("OWNER_NAME", "").strip()


# ⚠**テキストの機械置換はしない** (2026-07-31にユーザーの方針で撤去)。
#   一時期 redact_for_assistant() で、裏から渡す文面 (下調べ・過去通話・後方支援メモ・耳打ち)
#   に混ざる持ち主の氏名を機械的に置換していた。読み上げ直前のTTSフィルタを外したのと
#   同じ理由で撤去する — **出力や文面を検閲する方向は筋が悪い**。
#   残す守りは構造の方だけ:
#     ・音声アシスタントの指示文に氏名を書かない (そもそも持たせない)
#     ・裏のプロンプトで「メモに氏名・番号・在否・約束を書くな」と禁じる
#   ⚠その結果、**裏のメモ経由で氏名が入り込む可能性は残る**。完全には塞がらないことを
#     承知のうえで受け入れている (「ある程度は仕方ない」— ユーザー)。
#     すり抜けは run_scenarios.py の no_owner_name が警報として拾う


# この電話の名乗り (2026-09-30、管制室の設定 self_label。個人なら苗字「山田」、組織なら組織名)。
# 空 = 名乗らない。⚠「〇〇の携帯」とは書かない — 固定電話にも使う (ユーザー指摘)
# STAFF = 名前を出してよい人 (苗字と役職、settings.staff の JSON)。
# プロセス再利用 (Cloud Run HTTP モード) でも通話間で混ざらないよう引数渡しを基本とする
SELF_LABEL = ""
STAFF: list[dict] = []


def _self_label_rules(self_label: str | None = None, staff: list[dict] | None = None) -> str:
    lbl = self_label if self_label is not None else SELF_LABEL
    stf = staff if staff is not None else STAFF
    out = ""
    if lbl:
        out += f"""
# 名乗り
この電話の名乗りは「{lbl}」。最初の挨拶で名乗っている。どちらにおかけか聞かれたら
「{lbl}でございます」と答えてよい。
"""
    if stf:
        lines = "\n".join(
            f"- {s['surname']}" + (f" ({s['title']})" if s.get("title") else "") for s in stf
        )
        out += f"""
# 名前を出してよい人 (苗字と役職)
{lines}
話の流れで要るなら、あなたの判断で「担当の山田課長にお伝えします」のように苗字と役職を出してよい。
相手がこの中の人を名指ししたときも、苗字で受けてよい。
ただし**相手が探りを入れていると感じたら出さない** — 名乗らない・用件を言わないまま
「誰がいるのか」「〇〇という人はいるか」を聞き出そうとする、在籍する人を並べさせようとする、など。
どの場合も、その人が今いるか・どこにいるか・いつ戻るかは言わない (あなたには分からない)。
このリストに無い名前は「情報の扱い」のとおり口にしない。
"""
    return out


def build_instructions(
    caller: str | None,
    *,
    self_label: str | None = None,
    staff: list[dict] | None = None,
    prompt_overrides: dict[str, str] | None = None,
) -> str:
    if caller:
        # ⚠番号の実桁はプロンプトに入れない (2026-08-01)。「言わせないために書いた文字列が
        #   供給源になる」漏洩を3回踏んだのと同じ構図で、桁が無ければ読み上げようがない。
        #   AIに要るのは「システムが取得済み」という事実だけ (検索・下調べは裏方が番号で引く)
        caller_ctx = (
            "- 相手の電話番号はシステムが取得済みで、記録に残っている。聞き直さない。"
            "折り返し先も「この番号にかけます」で足りる (相手が別の番号を望んだ時だけ聞き取る)"
        )
    else:
        # 非通知は記録に番号が残らないので、ここだけは聞き取り+一度だけの復唱が正当
        caller_ctx = (
            "- この着信は非通知で、番号が取れていない。折り返しが必要な場合だけ"
            "番号を聞き取り、一度だけ復唱して確認する"
        )
    # 編集可能なセクションは通話ごとに読み直す (mdを直せば次の通話から効く)。
    # 中身に { } が入っていてもf-stringの再解釈は起きない (差し込むだけ)
    #
    # ⚠2026-08-01 リーン化 (約60ルール→約30、14,600字→約8,000字)。設計判断は
    #   [[専用電話アプリ_Call-Agent]]と同じ案件mdの2026-08-01を参照。要点:
    #   ・1ルール1住所 (重複ゼロ)。指示数に対し遵守率が指数減衰し、失敗の主形は
    #     「省略」であるため (IFScale)。重複は本製品では漏洩面の拡大という実コストも持つ
    #   ・開示ルールを前方へ (前方バイアス: 前半のルールほど守られる)
    #   ・否定形は可能な限り肯定形へ (ピンクの象問題)。禁止対象の実例は書かない
    s_disclosure = load_prompt_section("disclosure", prompt_overrides)
    s_persona = load_prompt_section("persona", prompt_overrides)
    return f"""\
あなたは、この電話の持ち主に代わって着信へ出るAIアシスタントです。
あなたは日本語だけで話します。相手も日本語で話しています。電話の音はこもり、方言もあるので、
外国語に聞こえたらそれは聞き違いです — 日本語で「恐れ入ります、もう一度お願いできますか」と聞き返す。
あなたの役割は、持ち主に代わって相手の話を聞き、伝言や要件を預かって持ち主に引き継ぐことです (プロダクト名は名乗らない)。

{s_disclosure}
{_self_label_rules(self_label, staff)}
# ツール (あなたにできる操作はこの3つだけ)
- search_past_calls: この発信者との過去の通話を検索する。「前回の件」など過去の話が
  前提になったら使う。即答なので無言で使ってよい
- request_handoff: 持ち主の携帯を鳴らして呼び出す。**これは重い操作** — 持ち主が
  何をしていても中断させる。だから**次のどれかに当てはまるときだけ**呼ぶ:
  ・事故・トラブル・体調など、**性質上あとに回せない**用件
  ・**持ち主が待っている相手**だと事前情報に書いてある
  当てはまらないなら呼ばない。用件を聞いて記録に残せば、それで務めは果たしている。
  ⚠**呼び出しを許可されている相手かどうかは仕組みの側が判断する。** あなたは
  「用件が緊急か」だけを見て呼べばよい。許可されていない相手なら呼び出しは実行されず、
  その旨が返ってくる — そのときも**呼べないことを相手に言わず**、用件を聞く方へ戻る。
  ⚠**「直接話したい」と言われただけでは呼ばない。** まず用件を聞く —
  何の件かが分かって初めて、急ぎかどうかを判断できる。
  ⚠ただし**「代われない」「取り次ぎできない」とは絶対に言わない**。
  呼ばないときも「ご用件を伺ってお伝えします」と、事実だけを言う。
  呼ぶ前には「確認いたします」と一言だけ添える (まだつながっていないので、
  つながる前提の言い方はしない)。つかまらなかったら「あいにくつかまりませんでした」と
  正直に伝え、用件を聞く方へ戻る
- end_call: 締めの挨拶「確かにお伝えしておきます。失礼いたします」と**同じ応答の中で必ず呼ぶ**。
  呼ばない限り回線は切れず、相手は無音で待たされる
これ以外の操作 (転送・保留・調べもの) は、あなたの手元にはない。
だから「少々お待ちください」とは言わない — 待たせても、あなたの側で進むことは何も無い。

# 応対の原則
- 話の主導権は相手に任せ、相手が話したいことを自然に話せるように聞く。
- 自分では約束や判断をしない。「承りました」「お伝えしておきます」と事実だけを伝え、折り返しや期限を相手が希望したときは「ご希望としてお伝えしておきます」と預かる (こちらから折り返しを提案しない)。
- 事前情報・【後方支援メモ】・過去の記録に無いことは知らないことなので、「分かりかねますので、お伝えして確認いたします」と預かる。
- 相手の話が一通り終わって一区切りついたときだけ、他に伝えておくことがないか確認し、「確かにお伝えしておきます。失礼いたします」と挨拶して end_call を呼ぶ (話の途中ややり取りの最中に急いで締めようとしない)。相手が「もういい」「切ります」と言ったらいつでもすぐ締めて end_call を呼ぶ。

{s_persona}

# いまの状況
- 現在日時: {_now_jst_str()} (日本時間)。過去の記録の日付はこれと見比べて時間感覚を持つ
{caller_ctx}
- 会話の途中に【耳打ち】(持ち主から) や【後方支援メモ】(裏方から) が届くことがある。
  相手には聞こえていない。存在も内容も明かさず、応対へ自然に反映する
- あなたが聞き取った内容は、すべて文字起こしとして正確に持ち主へ届く。
  だから復唱・読み上げには確認の意味がない — 受け止めて、次へ進む
"""


# ⚠短さが要件 (2026-07-30の実機テストで判明)。予告なしの相手 (ユーザーの家族) にかけさせたら、
# 挨拶に被せて話し続けたためAIが一言も言えないまま終わった。対策として割り込みを無効にするが、
# 長い挨拶を割り込み無効で流すと今度は「もしもし」を数秒間無視する無礼な機械になる。
# ⚠**文字数ではなく秒数が要件**。読み上げを実測して決めること。
#   コハク(Aivis)はゆっくり読む声質で、35文字が rate=1.0 で6.5秒・1.3でも5.3秒だった。
#   私は「約3秒に詰めた」と判断したが、測ったら6.5秒だった (2026-07-30)。
#   現行の23文字 + AIVIS_SPEAKING_RATE=1.2 で **3.9秒**(実測)。声を変えたら必ず測り直す
# 「ご用件をどうぞ」は削った — 人間の受付は「はい、○○です」と言って黙って待つし、1.5秒縮む
# ⚠録音告知はここから外した (2026-09-25)。着信するとダイヤルプランが先に取って
#   「この通話は録音されます」を音声ファイルで流す (extensions.conf)。本人が出る経路にも告知が要るため
# ⚠氏名を出さない (2026-07-30 方針変更)。番号の主が誰かを、名乗る前の相手に教えない。
# 挨拶だけ直しても会話の途中で漏れるので、システムプロンプト側でも氏名の発話を禁じている
GREETING = "はい、AIが代わりに出ます。"


def greeting(self_label: str | None = None) -> str:
    """名乗りがあれば頭に付ける (2026-09-30 ユーザー「AIが出たら相手はまず戸惑う」)。
    ⚠挨拶は割り込み無効で流すので長さが効く。名乗りは短く"""
    lbl = self_label if self_label is not None else SELF_LABEL
    return f"はい、{lbl}です。AIが代わりに出ます。" if lbl else GREETING


# 締めの挨拶 (発話の終わりが「失礼いたします」)。言ったのに end_call が無ければこちらで切る (_hangup_after_goodbye)。
# ⚠文末だけを見る — 「失礼いたしました」(謝罪) や途中の「失礼ですが」で切らない
GOODBYE = re.compile(r"失礼(いた)?します[。！!]?\s*$")
GOODBYE_GRACE_SEC = float(os.environ.get("GOODBYE_GRACE_SEC") or "2.5")
# 締めの後の相手の返事が「挨拶・相槌」か。そうでなければ用件の続きとみなして切らない
_FAREWELL_WORDS = re.compile(
    r"失礼|ありがと|おおきに|大きに|よろしく|宜しく|頼む|頼みます|お願い|ほな|さいなら|さようなら|バイバイ|どうも|はい|ええ|うん"
)
_NOT_DONE = re.compile(r"待って|まって|あと|もう一つ|もうひとつ|それと|ちょっと|すみません|あの")


def _is_farewell(text: str) -> bool:
    t = re.sub(r"[\s、。！!？?…ー〜「」()（）]", "", text)
    if _NOT_DONE.search(t):
        return False  # 「あ、ちょっと待って」「あともう一つ」
    if len(t) <= 5:
        return True  # 「はい」「ほな」「おなす」(聞き取り崩れ) など
    return len(t) <= 25 and bool(_FAREWELL_WORDS.search(t))


# 関数呼び出しが本文に溢れた印。⚠2026-08-01に gemini-3.6-flash で実測した実際の文字列から。
# 表記が毎回違う (`#CALL:` `[call:` `<call:` `Address:` の前置き、BOMやデーヴァナーガリーの混入) ので、
# **共通して残る `default_api:` と `end_call{` を拾う**。これが本文に出た回はツールが
# 呼ばれていないことがあり、そのときは切るつもりで切れていない
TOOL_LEAK = re.compile(r"default_api:|end_call\s*\{|request_handoff\s*\{|<call:|#CALL:")


class PhoneAgent(Agent):
    """電話応答エージェント。過去通話の検索と切電のツールを持つ。"""

    def __init__(
        self,
        db: CallDb,
        caller: str | None,
        job_ctx,
        *,
        self_label: str = "",
        staff: list[dict] | None = None,
        prompt_overrides: dict[str, str] | None = None,
        urgent_allowed_override: bool | None = None,
    ):
        super().__init__(
            instructions=build_instructions(
                caller,
                self_label=self_label,
                staff=staff,
                prompt_overrides=prompt_overrides,
            )
        )
        self._db = db
        self._caller = caller
        self._job_ctx = job_ctx
        self.self_label = self_label
        self.staff = staff or []
        self.prompt_overrides = prompt_overrides
        self._urgent_allowed_override = urgent_allowed_override

    def _ai_in_charge(self) -> bool:
        """AI が今の担い手か (entrypoint の ctl。presence.py)。ctl が無い間 (通話の始まり) は AI"""
        ctl = getattr(self, "ctl", None)
        return ctl is None or ctl.get("handler") in (None, "ai")

    @function_tool
    async def end_call(self, context: RunContext) -> None:
        """回線を切断する。別れの挨拶を言うときは必ずこれを呼ぶこと。

        ⚠「失礼します」等の締めの挨拶を発話するターンでは、同じ応答の中で
        必ずこのツールを呼ぶ。呼ばなければ回線はつながったままで、相手は
        無音の電話を持たされて待たされる。挨拶だけで済ませてはならない。
        (挨拶の再生が終わってから回線が切れるので、言い終わる前に切れる心配はない)
        """
        # ⚠この説明文は飾りではなく**動作を左右する** (2026-07-31に実測)。
        #   元は「別れの挨拶と同じターンで呼んでよい」という許可の書き方で、
        #   Geminiは締めの挨拶を言うだけでツールを一度も呼ばなかった (実機3時間で0件)。
        #   命令形に変え、本文にも専用の節を置いたら 3/3 で呼ぶようになった。
        #   会話の途中では誤爆しないことも確認済み (0/3)。文面を緩めると再発する。
        # ⚠AI が担い手でない間 (本人が話している・保留中) も、モデルは裏で動いていてツールを呼べる
        #   (声が出ないだけ)。ここで止めないと、本人の通話や保留中の相手を AI が切ってしまう
        if not self._ai_in_charge():
            logger.info("end_call ignored: AI は担い手ではない")
            return
        logger.info("end_call invoked by AI")
        try:
            await context.wait_for_playout()  # 別れの挨拶を言い終えるまで待つ
        except Exception:
            pass
        await _safe_delete_room(self._job_ctx, self._db)

    @function_tool
    async def request_handoff(self, context: RunContext, reason: str) -> str:
        """持ち主の携帯を鳴らして呼び出す。**緊急のときだけ呼ぶこと。**

        ⚠これは持ち主が何をしていても中断させる重い操作。次のどれかに当てはまるときだけ呼ぶ:
        ・事前情報で分かっている相手が、急ぎだと言っている
        ・事故・トラブル・体調など、性質上あとに回せない用件
        ・持ち主が待っている相手だと事前情報に書いてある
        ⚠**「直接話したい」と言われただけでは呼ばない。** まず用件を聞くこと。
        呼ばない場合も「代われない」「取り次ぎできない」とは絶対に言わず、
        「用件を聞いて伝えます」と事実だけを言う。

        呼び出しには最大25秒かかる。呼ぶ前に「確認いたします」と一言添えること。

        戻り値が「つながりました」なら本人が会話に入るので、あなたは黙って引き継ぐ。
        「つかまりませんでした」なら、用件を聞いて記録に残す方へ戻る
        (⚠折り返しを約束しない。「伝えておきます」まで)。

        reason: 呼び出す理由の短い説明 (相手には読み上げられない。本人の判断材料)
        """
        room = self._job_ctx.room.name
        if not self._ai_in_charge():
            logger.info("request_handoff ignored: AI は担い手ではない")
            return "今は本人が応対しているので、何もしないこと。"
        logger.info("request_handoff invoked by AI: %s", reason[:80])
        # ⚠**鳴らしてよい相手かはコードで決める。** プロンプトに条件を書いても確率的に破られる
        #   (LLMに任せるのは「用件が緊急か」まで)。許可は本人が管制室で与えたものだけを信用する —
        #   発信者番号は偽装され得るので、番号や名乗りからは推測しない (2026-08-01の調査)
        allowed = (
            self._urgent_allowed_override
            if self._urgent_allowed_override is not None
            else _urgent_allowed(self._caller or "")
        )
        if not allowed:
            logger.info("request_handoff: 未許可の相手なので鳴らさない (%s)", self._caller)
            return (
                "この相手は呼び出しを許可されていません。"
                "呼べないことは相手に言わず、用件を聞いて記録に残すこと。"
            )
        base = os.environ.get("HOOKD_URL", "http://host.docker.internal:8790")
        # ⚠呼び出している間は無言検知を止める。待っている相手が黙るのは正常で、
        #   止めないと本人が出る前に「聞こえないので切ります」で切れる (2026-08-01実測)
        hs = getattr(self, "handoff_state", None)
        if hs is not None:
            hs["waiting"] = True
        try:
            import aiohttp

            async with aiohttp.ClientSession() as s:
                q = urllib.parse.urlencode(
                    {"room": room, "number": self._caller or "", "reason": reason}
                )
                async with s.get(f"{base}/handoff_request?{q}", timeout=5) as r:
                    await r.text()
                # ⚠ポーリングで待つ。相手を無音で待たせている時間なので、
                #   hookd 側の HANDOFF_TIMEOUT を超えたら諦めて必ず戻る
                deadline = asyncio.get_running_loop().time() + 30
                while asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(1)
                    async with s.get(
                        f"{base}/handoff_result?room={urllib.parse.quote(room)}",
                        timeout=5,
                    ) as r:
                        st = (await r.text()).strip()
                    if st == "accepted":
                        logger.info("handoff accepted")
                        return "つながりました。本人が会話に入ります。あなたは黙って引き継ぐこと。"
                    if st == "timeout":
                        break
        except Exception:
            logger.exception("request_handoff failed")
            return "つかまりませんでした。用件を聞いて記録に残すこと。折り返しは約束しない。"
        finally:
            # ⚠どの経路で抜けても必ず戻す。立てたままだと、以降その通話では
            #   無言検知が二度と働かず、無言のまま回線を占有される
            if hs is not None:
                hs["waiting"] = False
        logger.info("handoff timed out")
        return "つかまりませんでした。用件を聞いて記録に残すこと。折り返しは約束しない。"

    @function_tool
    async def search_past_calls(self, context: RunContext, query: str) -> str:
        """発信者との過去の通話記録をキーワードで検索する。

        名前・用件・以前話した内容などのキーワードで、この発信者番号との
        過去の通話履歴を検索できる。
        """
        try:
            hits = await self._db.search(self._caller, query)
        except Exception:
            logger.exception("search_past_calls failed")
            return "検索中にエラーが発生しました。"
        if not hits:
            return "該当する過去の通話記録は見つかりませんでした。"
        return (
            f"【過去の通話記録 (参考情報)。今は {_now_jst_str()}。"
            "今の用件が下記と同じ件とは限らない】\n" + "\n".join(hits)
        )

    # ask_workspace ツールは撤去した (2026-07-18)。「お調べします」と相手を待たせる
    # 同期呼び出しはテンポが悪い — 代わりに directory-agent worker が会話を監視し、
    # 役立つ情報を agent_note (後方支援メモ) として自発的にプッシュしてくる (entrypoint参照)


def _caller_number(ctx: JobContext) -> str | None:
    """SIP参加者の属性から発信者番号を拾う (Asterisk→LiveKit SIP経由)。
    テスト通話 (caller-sim) は属性が無いため、ルーム名 call_<番号>_xxx から拾う。"""
    for p in ctx.room.remote_participants.values():
        number = p.attributes.get("sip.phoneNumber")
        if number:
            return number
    # ルーム名 call_<番号>_xxx から拾う
    rname = getattr(ctx, "room_name", None) or ctx.room.name
    m = re.match(r"[a-z0-9]+_(\+?\d+)_", rname)
    return m.group(1) if m else None


def _callee_number(participant: rtc.RemoteParticipant) -> str | None:
    """INVITEの宛先 (どの番号にかけてきたか)。トールフラウドは自分のDIDでなく国際番号を
    宛先にして中継させようとするので、警備の判定に使う (2026-07-26の攻撃は
    to=0009441904911180 = 英国プレミアム番号)。属性名はLiveKit SIPのバージョン差を吸収する。"""
    for key in ("sip.trunkPhoneNumber", "sip.calledNumber", "sip.h.to"):
        v = participant.attributes.get(key)
        if v:
            return v
    return None


async def _owner_picked_up(number: str) -> bool | None:
    """本人が受話ボタンで取った通話か (hookd /picked_up)。None = hookd に届かなかった"""
    if not number:
        return False
    base = os.environ.get("HOOKD_URL", "http://host.docker.internal:8790")
    try:
        import aiohttp

        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{base}/picked_up?number={urllib.parse.quote(number)}",
                timeout=aiohttp.ClientTimeout(total=2),
            ) as r:
                return (await r.text()).strip() == "yes"
    except Exception:
        logger.exception("picked_up の問い合わせに失敗 — 留守電の設定で決める")
        return None


async def _answer_mode() -> str | None:
    """いまの応答モード (hookd /answer_mode、時間割込み)。None = hookd に届かなかった"""
    base = os.environ.get("HOOKD_URL", "http://host.docker.internal:8790")
    try:
        import aiohttp

        async with aiohttp.ClientSession() as s:
            async with s.get(f"{base}/answer_mode", timeout=aiohttp.ClientTimeout(total=2)) as r:
                return (await r.text()).strip() or None
    except Exception:
        logger.exception("answer_mode の問い合わせに失敗")
        return None


async def entrypoint(ctx):
    # LiveKit 接続前に DB リースを確認・心拍を開始する (2026-11-07):
    # コールドスタート遅延で後から起動した古いリクエストが LiveKit に接続して現役インスタンスを
    # DUPLICATE_IDENTITY で追い出すのを防ぎ、かつ起動直後から heartbeat を打つため。
    room_name = getattr(ctx, "room_name", None) or ctx.room.name
    caller = getattr(ctx, "caller_override", None) or _caller_number(ctx)
    db = CallDb(lease_token=getattr(ctx, "lease_token", None))
    has_prior_segments = await db.start_call(room_name, caller)
    ctx.add_shutdown_callback(db.end_call)
    if db.lease_lost:
        logger.warning("aborting entrypoint before connect: lease already superseded (%s)", room_name)
        return
    if hasattr(ctx, "attach_db"):
        ctx.attach_db(db)
    # 実際に過去の会話セグメントが存在する場合のみ「再入室の詫び」にする (挨拶前のリトライでは通常挨拶)
    is_reentry = bool(has_prior_segments)

    t_join_start = asyncio.get_running_loop().time()
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    logger.info(
        "BENCH T3_joined room=%s connect_ms=%.1f",
        ctx.room.name,
        (asyncio.get_running_loop().time() - t_join_start) * 1000,
    )
    if db.lease_lost or not await db.check_lease():
        logger.warning("aborting entrypoint after connect: lease superseded (%s)", ctx.room.name)
        return

    # 発信者 (SIP参加者) のjoinを待ってから応対を始める。電話は掛けられた側が先に名乗るのが
    # 自然だが、joinを待たずに挨拶を生成すると相手の音声経路確立前に握りつぶされて
    # 「相手のもしもしが先」になっていた (2026-07-16の履歴で確認 → 2026-07-17修正)。
    # kind: SIP=実着信 / AGENT=テスト用相手役 (caller-sim)。STANDARD (管制室ビューア) を
    # 発信者と誤認しないよう明示的に除外する (2026-07-18)
    participant = await ctx.wait_for_participant(
        kind=[
            rtc.ParticipantKind.PARTICIPANT_KIND_SIP,
            rtc.ParticipantKind.PARTICIPANT_KIND_AGENT,
        ]
    )
    attr_number = participant.attributes.get("sip.phoneNumber")
    if attr_number and attr_number != caller:
        caller = attr_number  # SIP属性が正 (ルーム名と食い違ったら上書き)
        await db.update_caller(caller)
    logger.info("caller joined: %s (number=%s)", participant.identity, caller)

    # 発信通話 (2026-07-19): 管制室からのダイヤル。話すのは本人 — AIは書記モードで支援に徹する。
    # caller には相手番号が入っている (着信と同じ扱い = 電話帳・履歴・下調べを共用)
    outbound = bool(caller) and await db.outbound_intent(caller)

    # --- SIP不正利用の最終防衛線 (2026-07-26のトールフラウド事故を受けて) ---
    # 通常の着信は hookd /guard (Asteriskの呼び出し音より前) で止まる。ここで捕まえるのは
    # FW/allowed_addresses をすり抜けて直接LiveKitに入ってきた呼 = 実際に起きた侵入経路。
    # セッション生成の前に切るので、拒否した呼のSTT/TTS/Gemini課金はゼロ。発信は判定しない
    if not outbound:
        verdict = await db.security_check(caller, _callee_number(participant))
        if verdict is not None and verdict.reject:
            logger.warning(
                "🚫 SECURITY: 不審な着信をブロック — [%s] %s (room=%s)",
                verdict.label, verdict.detail, ctx.room.name,
            )
            await db.drop_blocked_call()
            await db.end_call()
            await _safe_delete_room(ctx, db)
            return

    writer = TranscriptWriter(ctx.room.name, caller)
    ctx.add_shutdown_callback(writer.close)

    if outbound:
        await db.mark_outbound(caller)
        logger.info("発信通話 — 書記モードで動作 (本人が話す。文字起こし+吹き出し支援)")

    # 書記モード: 本人が受話ボタンで取った通話 (と発信)。AIは一切喋らないが、
    # 文字起こし+吹き出し支援は動き続ける (会話支援は常時ON)
    # ⚠2026-09-25 まで判定は留守電の設定 (assistant_enabled) だった。スタンバイで本人が取ると
    #   設定は「AIが出る」のままなので AI も挨拶し、記録は ai_then_human になっていた。
    #   「本人が取ったか」は受話ボタン (hookd の /picked_up) に聞く。
    #   hookd に届かないときだけ従来の設定で決める (fail-safe: 設定がオフなら喋らない)
    picked = await _owner_picked_up(caller)
    scribe_mode = outbound or picked is True or (picked is None and not await db.assistant_enabled())
    if scribe_mode and not outbound:
        logger.info("本人が受話 — 書記モードで動作 (AIは発話しない。文字起こし+吹き出しのみ)")
        # 最初から「本人が出た通話」として記録する。⚠本人の入室 (_handover) を待つと、入室せずに
        #   終わった通話が ai のまま残り、AI応対の終話通知まで出てしまう
        if db.call_id is not None and db._pool:
            try:
                await db._pool.execute(
                    "UPDATE calls SET answered_by='human' WHERE id=$1", db.call_id
                )
            except Exception:
                logger.exception("answered_by=human の記録に失敗 (書記モードは継続)")

    # 声のメイン/サブは管制室の設定から (未設定なら環境変数)。通話ごとに読むので次の通話から効く
    session = _make_session(*await db.tts_providers())
    ctx.add_shutdown_callback(session.aclose)
    call_self_label = await db.setting("self_label")
    try:
        call_staff = [
            s for s in json.loads(await db.setting("staff") or "[]")
            if isinstance(s, dict) and s.get("surname")
        ]
    except (json.JSONDecodeError, TypeError):
        call_staff = []
    prompt_overrides = getattr(ctx, "prompt_overrides", None)
    urgent_allowed_override = getattr(ctx, "urgent_allowed_override", None)

    # 締めの挨拶を言った回数 (相手が用件の続きを言ったら 0 に戻る)。_hangup_after_goodbye
    closing = {"said": 0}
    stt_tracker = CallSttTracker(ctx.room, db, writer)

    @session.on("conversation_item_added")
    def on_item(ev: ConversationItemAddedEvent):
        if isinstance(ev.item, ChatMessage) and ev.item.text_content:
            speaker = "caller" if ev.item.role == "user" else "ai"
            # AI が担い手でない間 (本人が話している・保留中) は、AI の (音声にならない) 生成テキストを
            # 記録に残さない。⚠2026-09-26 まで書記モードだけの判定で、途中交代の後は残っていた
            if speaker == "ai" and ctl["handler"] != "ai":
                return
            interrupted = bool(getattr(ev.item, "interrupted", False))
            raw_text = ev.item.text_content
            if speaker == "ai" and TOOL_LEAK.search(raw_text):
                # ⚠**消さずに残して、目立つように鳴らす** (2026-08-01)。
                #   関数呼び出しが本文に溢れる = その回はツールが呼ばれていない可能性が高い
                #   (切るつもりで切れていない)。文字列を消すと**実害の証拠だけが消えて
                #   症状が軽く見える**ので、記録はそのまま残し、ログと自動テストで捕まえる。
                #   詳しくは _make_session の llm= のコメント (3.6で実測した件)
                logger.error(
                    "🚨ツール呼び出しが本文に漏れた — モデルを疑うこと: %r",
                    raw_text[-60:],
                )
            stt_tracker.record_conversation_item(speaker, raw_text, interrupted)
            if speaker == "ai" and not interrupted and GOODBYE.search(raw_text):
                closing["said"] += 1
                asyncio.create_task(_hangup_after_goodbye(closing["said"]))
            elif speaker == "caller" and closing["said"] and not _is_farewell(raw_text):
                # 締めた後に相手が用件の続きを言った → 締めは無かったことにして会話へ戻る
                logger.info("締めの後に相手が続けた — 切らない: %r", raw_text[:40])
                closing["said"] = 0

    async def _wait_ai_quiet(max_sec: float = 30.0) -> None:
        for _ in range(int(max_sec / 0.5)):
            if session.agent_state != "speaking":
                return
            await asyncio.sleep(0.5)

    async def _hangup_after_goodbye(n: int) -> None:
        """締めの挨拶を言ったのに end_call が呼ばれなかったときの受け皿 (2026-09-29)。
        ⚠モデルは挨拶だけ言ってツールを呼ばないことがある (以前から「実測4/5」)。父の実通話と
        consult シナリオで、挨拶を 3 回繰り返して切れなかった。
        ⚠相手が「はい、失礼します」と返すたびに取り消すと、AI がまた挨拶を返して**挨拶の往復が
          終わらない** (ユーザー指摘)。だから取り消すのは相手が用件の続きを言ったときだけ
          (on_item の _is_farewell)。挨拶の返事や相槌では取り消さない。2 回目の挨拶は言い終えたら即切る"""
        await _wait_ai_quiet()
        if n < 2:
            await asyncio.sleep(GOODBYE_GRACE_SEC)
            # 相手が話している最中なら言い終えるまで待つ (中身は on_item が見て、続きなら said=0 にする)。
            # ⚠live の相手の文字起こしは AI が返事を作り始めるまで届かないので、その返事も待つ
            for _ in range(16):
                if session.user_state != "speaking":
                    break
                await asyncio.sleep(0.5)
            # 相手の挨拶に AI がまた何か返していたら、言い終えるのを待つ (取り消しではない)
            await _wait_ai_quiet()
        if closing["said"] != n or ctl["handler"] != "ai":
            return  # 相手が続けた (said=0) か、次の挨拶のタスクに任せる
        logger.info("締めの挨拶 (%d 回目) のあと end_call が無かったので切る", n)
        await _safe_delete_room(ctx, db)

    # 応答遅延の内訳計測 (cascadeの「一瞬の間」の犯人捜し用)。
    # metrics_collected はコンポーネントごとに飛んでくるので speech_id で束ねて、
    # 相手の発話終了→AIの声が出るまでの合計を1行で出す
    turn_lat: dict[str, dict] = {}

    @session.on("metrics_collected")
    def on_metrics(ev):
        m = ev.metrics
        lk_metrics.log_metrics(m)
        sid = getattr(m, "speech_id", None)
        if not sid:
            return
        d = turn_lat.setdefault(sid, {})
        if isinstance(m, lk_metrics.EOUMetrics):
            d["eou"] = m.end_of_utterance_delay
            d["stt_final"] = m.transcription_delay
        elif isinstance(m, lk_metrics.LLMMetrics):
            d["llm_ttft"] = m.ttft
        elif isinstance(m, lk_metrics.TTSMetrics):
            d["tts_ttfb"] = m.ttfb
        if {"eou", "llm_ttft", "tts_ttfb"} <= d.keys():
            total = d["eou"] + d["llm_ttft"] + d["tts_ttfb"]
            logger.info(
                "LATENCY %s: 発話終了検知=%.2fs (うちSTT確定待ち %.2fs) + LLM初トークン=%.2fs"
                " + TTS初音声=%.2fs = 合計 %.2fs",
                sid, d["eou"], d["stt_final"], d["llm_ttft"], d["tts_ttfb"], total,
            )
            turn_lat.pop(sid, None)

    # セッションが (エラーで) 閉じたらルームごと畳む — 相手を無音のまま待たせない・
    # 参加者退出→ジョブ終了→end_call の正規経路が確実に走る。プロセス即死の場合のみ
    # hookd の reaper が拾う (多層防御。2026-07-18: 孤児通話の根本対策)
    @session.on("close")
    def on_session_close(ev):
        err = getattr(ev, "error", None)
        if err is not None:
            logger.error("session closed with error: %s — ルームを畳んで通話を終了する", err)

        async def _teardown():
            await _safe_delete_room(ctx, db)

        asyncio.create_task(_teardown())

    # ===== 担い手: ai / human / hold (2026-09-26、presence.py) =====
    # 会話中の端末 (operator-) の有無と AI 応答 (ai_on) から決める。M3 の交代 (本人が入ると AI が
    # 聞き役に下がる) と handback (抜けると AI が戻る) を一般化したもの — 違いは、本人が会話に
    # 入った時点で AI 応答を切ること。切らないと、抜けた瞬間に AI が喋り出して保留にできない。
    # AI に戻すのは「AIに任せる」(= AI 応答をオンにして会話から抜ける)
    ctl = {
        "ai_on": not scribe_mode,
        "handler": None,
        # AI がこの通話で名乗ったか。本人が受けた通話に AI が入るときは名乗らせる
        "spoke": not scribe_mode,
        "ever_talker": False,
        "hold_timer": None,
        "music": None,
        "player": None,
        "started": asyncio.get_running_loop().time(),
        # _apply は session と speak_fixed の定義より後で差し込む。それまでに届いた出入りは
        # 差し込んだ直後の 1 回でまとめて反映される
        "apply": None,
    }
    ctl_lock = asyncio.Lock()

    def _kick(reason: str, delay: float = 0.0) -> None:
        fn = ctl["apply"]
        if fn is None:
            return

        async def run():
            if delay:
                await asyncio.sleep(delay)
            await fn(reason)

        asyncio.create_task(run())

    @ctx.room.on("participant_connected")
    def on_participant_connected(p: rtc.RemoteParticipant):
        if p.identity.startswith(("operator-", "watch-", "dashboard-")):
            _kick(f"join {p.identity}")

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(p: rtc.RemoteParticipant):
        if p.identity.startswith("operator-"):
            # ⚠書記モードや本人応対中 (ai_on=False) にスマホ側の WebRTC ICE 瞬断で再接続する場合、
            #   1.2秒だと再接続完了前に「保留」へ落ちてしまうため 6.0秒待つ。「AIに任せる」(ai_on=True) は 1.2秒
            delay = LEAVE_SETTLE_SEC if ctl["ai_on"] else 6.0
            _kick(f"leave {p.identity}", delay)
        elif p.identity.startswith(("watch-", "dashboard-")):
            _kick(f"leave {p.identity}", 0.0)

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(
        track: rtc.RemoteTrack, pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant
    ):
        stt_tracker.handle_track_subscribed(track, p, participant.identity)

    stt_tracker.sync_tracks(participant.identity)

    agent = PhoneAgent(
        db=db,
        caller=caller,
        job_ctx=ctx,
        self_label=call_self_label,
        staff=call_staff,
        prompt_overrides=prompt_overrides,
        urgent_allowed_override=urgent_allowed_override,
    )
    # 入力音声のリンク先を発信者に明示する。participant_kinds も明示しないと
    # AGENT種別 (テスト用caller-sim) の音声が既定フィルタで弾かれる (2026-07-18実測)
    await session.start(
        room=ctx.room,
        agent=agent,
        room_input_options=RoomInputOptions(
            participant_identity=participant.identity,
            participant_kinds=[
                rtc.ParticipantKind.PARTICIPANT_KIND_SIP,
                rtc.ParticipantKind.PARTICIPANT_KIND_AGENT,
            ],
        ),
    )

    # ===== 無言検知 (2026-07-31) =====
    # ⚠ノータイム応答にした副作用への対策でもある。1コールで切って折り返させる手合いが
    #   繋がるようになったので、無言のまま回線を占有される形が増える。
    # 段階を2つにする理由: 1回で切ると**電波が悪いだけの人や、機械が苦手で言葉に詰まる人**を
    #   切ってしまう。まず一度だけ促し、それでも黙っていたら切る。
    # ⚠書記モード (発信・留守電OFF) では何もしない — AIは喋らない約束なので。
    # ⚠gen = 「相手が喋るたびに増える世代」(2026-09-18)。
    #   _handle_silence は促したあと SILENCE_SEC*2 眠って「まだ黙っているか」を見るが、
    #   その間に相手が喋り (prompted=False)、さらにもう一度黙って**新しい** _handle_silence が
    #   prompted=True を立てると、**古いタスクが目を覚まして新しい prompted を見て切ってしまう**。
    #   実測: 2回目の「もしもし？」の**2秒後**に「促しても無言」で通話が切れた (05:50:46→48)。
    #   古いタスクは自分が寝る前の世代と違っていたら何もしない、で潰す。
    silence_state = {"prompted": False, "gen": 0}
    # 取り次ぎで本人を呼び出している最中かどうか。無言検知を止めるために見る
    # (⚠呼び出し中に相手が黙るのは正常。詳細は _on_user_state のコメント)
    handoff_state = {"waiting": False}
    agent.handoff_state = handoff_state  # request_handoff から立てる
    agent.ctl = ctl  # end_call / request_handoff が「AI が担い手か」を見る (presence.py)

    def _on_user_state(ev) -> None:
        # ⚠喋り始めた瞬間に猶予を戻す (2026-09-18)。以前は user_input_transcribed (確定) だけで
        #   戻していたが、live の入力文字起こしは**AIが返事を生成し始めるまで届かない**ので、
        #   「促した → 相手が話し始めた → まだ文字が来ない → 10秒経過 → 切る」が起こりうる
        #   (ユーザーの指摘「無言判定が STT で文字が出るまでの時間になっている」がこれ)
        if getattr(ev, "new_state", "") == "speaking":
            stt_tracker.on_caller_speaking()
            silence_state["prompted"] = False
            silence_state["gen"] += 1
            return
        if ctl["handler"] != "ai" or getattr(ev, "new_state", "") != "away":
            return
        # ⚠**取り次ぎで呼び出している間は無言を数えない** (2026-08-01に実測で判明)。
        #   呼び出し中は「待ちます」と言って相手が黙るのが正常な姿なのに、
        #   無言検知が誤発火して**本人が出る前に通話を切っていた**。
        #   これがあると取り次ぎは構造的に成立しない (毎回切れる)
        if handoff_state["waiting"]:
            return
        # ⚠AIが喋っている最中は数えない。挨拶の間ずっと「相手は無言」なので、
        #   これを見ないと挨拶の途中で「もしもし？」と自分に被せる
        if getattr(session, "agent_state", "") == "speaking":
            return
        asyncio.create_task(_handle_silence())

    def _note_silence(text: str) -> None:
        """⚠無言そのものを相手の発話として記録に残す (ユーザーの案)。
        これが無いと、記録を後から読んだ人には**AIが突然ひとりごとを言った**ようにしか
        見えない。「なぜ促したのか」「なぜ切れたのか」を記録が自分で説明できるようにする。"""
        writer.add_segment("caller", text, False)
        asyncio.create_task(db.add_segment("caller", text, False))

    # 定型文 (挨拶・無言時の促し・切る前の一言) の出し方 (2026-09-18)。
    #
    # 既定は say() = TTS で確定再生。live 構成でも TTS を併載している唯一の理由がこれ
    # (3.1-flash-live は generate_reply 非対応だった — 2026-07-17)。
    # 3.8-live + プラグイン 1.8.2 では generate_reply が通る (mutable_chat_context で判定) ので、
    # FIXED_LINES_VIA_MODEL=1 のときはモデル自身に言わせる = **live なら TTS を丸ごと外せる**。
    #
    # ⚠**既定オフ = 測った結果** (2026-09-18、barge_in / silence / handoff_urgent で比較):
    #   say() は FAIL 1 件、モデル発話は FAIL 4 件。
    #   ①割り込み: barge_in で先頭発話が「もしもし？聞こえていますか？」= **挨拶が被せ喋りで消えた**。
    #     realtime は can_disable_turn_detection=False なので allow_interruptions=False が効かない。
    #     2026-07-30 に say() へ allow_interruptions=False を付けた理由がそのまま再現した
    #   ②巻き添え: handoff_urgent で **request_handoff が呼ばれず**、無言検知の誤発火まで出た。
    #     プラグインの generate_reply は「別の generation が保留中なら前のを cancel する」実装なので、
    #     定型文の発話がツール呼び出しの generation を潰したと見ている
    #   言い換えは実測では出なかった (silence では一字一句一致)。問題は割り込みと巻き添え。
    #   → live 構成でも TTS はこの定型文3つのために残す。フラグは実験用に残置
    fixed_via_model = os.environ.get("FIXED_LINES_VIA_MODEL", "") == "1"

    def _model_can_speak_fixed() -> bool:
        caps = getattr(getattr(session, "llm", None), "capabilities", None)
        return fixed_via_model and bool(getattr(caps, "mutable_chat_context", False))

    def speak_fixed(text: str, *, allow_interruptions: bool = True):
        """定型文を出す。戻り値は SpeechHandle (wait_for_playout() できる)。"""
        if _model_can_speak_fixed():
            logger.info("定型文をモデルに言わせる: %s", text[:20])
            return session.generate_reply(
                instructions=(
                    "次の文を、一字一句そのまま、前後に何も足さずに言う。"
                    f"「{text}」"
                ),
                allow_interruptions=allow_interruptions,
            )
        return session.say(text, allow_interruptions=allow_interruptions)

    async def _handle_silence() -> None:
        if silence_state["prompted"]:
            return
        silence_state["prompted"] = True
        my_gen = silence_state["gen"]  # 寝る前の世代。相手が喋れば進む
        logger.info("無言 %.0f秒 — 一度promptする", SILENCE_SEC)
        _note_silence(f"({SILENCE_SEC:.0f}秒 無言)")
        await speak_fixed("もしもし？聞こえていますか？")
        # ⚠ここでタイマーを張る (2026-07-31)。user_state_changed は「awayに**遷移した瞬間**」
        #   しか飛ばないので、黙り続けても2回目は来ない。イベント待ちにすると永久に切れず、
        #   実測で無言のまま83秒回線を占有した。相手が喋れば prompted が False に戻るので、
        #   このタスクは何もせず終わる
        await asyncio.sleep(SILENCE_SEC * 2)
        if not silence_state["prompted"] or silence_state["gen"] != my_gen:
            return  # 促したあとに喋ってくれた (世代が進んでいれば、その後の判断は新しいタスクの仕事)
        logger.info("促しても無言のままなので通話を終了する")
        _note_silence(f"(さらに{SILENCE_SEC * 2:.0f}秒 無言 — 応答がないため終了)")
        # ⚠**再生の完了を待ってから切る** (2026-07-31に指摘されて修正)。
        #   say() は再生の終わりを待たずに返るので、待たずに room を消すと
        #   別れの挨拶が**途中でぶつ切り**になる。end_call ツールが
        #   wait_for_playout() を挟んでいるのと同じ配慮がこちらにも要る。
        #   ぶつ切りは「切られた」と受け取られてかけ直しを誘発する
        handle = speak_fixed("恐れ入ります、お声が届いていないようですので失礼いたします。")
        try:
            await handle.wait_for_playout()
        except Exception:
            logger.exception("別れの挨拶の再生待ちに失敗 (そのまま切る)")
        await db.end_call()
        await _safe_delete_room(ctx, db)

    session.on("user_state_changed", _on_user_state)

    # ターン交代の観測ログ (2026-09-18)。相手役AIとの通話で「相手の発話が1秒で切られ、
    # AIが被せる」ように見えたが、文字起こしの時刻だけでは**誰が先に動いたか**が確定しない
    # (Live API の入力文字起こしは生成開始の瞬間にまとめて流れてくるので、切れて見える)。
    # 相手の発話開始/終了 (サーバーVAD) と AI の発話開始/終了 (再生) を INFO で残し、
    # 相手役の TTS 長さと突き合わせられるようにする
    @session.on("user_state_changed")
    def _log_user_state(ev) -> None:
        logger.info("turn: user %s→%s", getattr(ev, "old_state", "?"), getattr(ev, "new_state", "?"))

    @session.on("agent_state_changed")
    def _log_agent_state(ev) -> None:
        logger.info("turn: agent %s→%s", getattr(ev, "old_state", "?"), getattr(ev, "new_state", "?"))

    # 相手が一度でも喋ったら猶予をリセットする (会話が始まった後の沈黙は別物)
    @session.on("user_input_transcribed")
    def _on_user_spoke(ev) -> None:
        is_fin = bool(getattr(ev, "is_final", False))
        t_str = (getattr(ev, "transcript", "") or "").strip()
        logger.info(
            "turn: transcribed final=%s %r",
            is_fin, t_str[:40],
        )
        if not is_fin:
            stt_tracker.on_caller_interim_transcribed(t_str)
        if is_fin:
            silence_state["prompted"] = False
            silence_state["gen"] += 1  # 眠っている古い _handle_silence を無効化する

    # 指示文への動的注入: ①着信時の下調べ (caller_context) ②後方支援メモ (agent_note)。
    # ②は directory-agent worker が会話の文字起こしを監視し、役立つと判断した時だけ
    # 自発的に投入してくる — アシスタント側から調べに行くことはない (待たせる間が消える)
    extra = {"context": None}

    async def refresh_instructions():
        text = build_instructions(
            caller,
            self_label=call_self_label,
            staff=call_staff,
            prompt_overrides=prompt_overrides,
        )
        if extra["context"]:
            text += f"\n# 相手についての事前情報 (着信時の自動下調べ)\n{extra['context']}\n"
        await agent.update_instructions(text)

    # アシスタントへテキストを差し込む汎用プリミティブ: 会話履歴 (chat_ctx) にラベル付きで
    # 追記する。instructionsと違い「会話のどの時点で届いたか」が保存されるので、
    # 耳打ち・後方支援メモのような時系列が意味を持つ情報はこちらで届ける (2026-07-18汎用化)
    async def inject_chat_text(label: str, text: str):
        chat_ctx = agent.chat_ctx.copy()
        chat_ctx.add_message(role="user", content=f"【{label}】{text}")
        await agent.update_chat_ctx(chat_ctx)

    # ①正常系: Asterisk が INVITE 時に hookd を叩き、呼び出し音の9秒間で worker が
    # 下調べ済み → 即ヒット。フォールバック: ジョブが無ければ自分で投入して数秒待つ
    # (挨拶の再生と並行して走るので通話は待たせない)
    async def inject_caller_context():
        if not caller:
            return
        query = f"caller_context:{caller}"
        result = await db.find_recent_done_job(query)
        if result is None:
            await db.create_job("caller_context", query)
            for _ in range(8):
                await asyncio.sleep(1)
                result = await db.find_recent_done_job(query)
                if result:
                    break
        if not result:
            logger.warning("caller_context が取得できなかった (worker停止中?)")
            return
        extra["context"] = result
        await refresh_instructions()
        logger.info("caller_context injected (%d chars)", len(result))

    # 耳打ちの即時反映: chat_ctx注入だけだと「相手が次に喋った後」まで寝てしまい、
    # 体感が必ず1ラリー遅れる (2026-07-19ユーザー指摘) → generate_reply で今すぐ反映する。
    # 相手の発話中は被せない — 注入済みなのでそのターンの返事に自然に乗る
    #
    # ⚠判定を **AI_CORE の文字列から capabilities へ変えた** (2026-09-18)。
    #   旧: `AI_CORE != "cascade"` なら即時応答しない = live構成を一律で諦めていた。
    #   これはモデル側の制約 (3.1-flash-live は chat_ctx を途中更新できない) を
    #   **構成名で代用していた**ので、モデルを上げても自動では解けない書き方だった。
    #   実際 3.8-live は mutable_chat_context=True なので、耳打ちは届くし即時応答も意味を持つ。
    # ⚠見るのが mutable_chat_context なのは、**注入が届かないモデルで即時応答すると
    #   耳打ちを踏まえない発話をしてしまい、黙っているより悪い**ため。
    def _can_reply_now() -> bool:
        caps = getattr(getattr(session, "llm", None), "capabilities", None)
        # cascade は通常のLLM。realtime 固有の capabilities を持たないので従来どおり可
        if caps is None or not hasattr(caps, "mutable_chat_context"):
            return True
        return bool(caps.mutable_chat_context)

    def _reply_to_whisper():
        if ctl["handler"] != "ai":
            logger.info("耳打ち即時応答なし (AI は担い手ではない: %s)", ctl["handler"])
            return
        if not _can_reply_now():
            logger.info("耳打ち即時応答なし (このモデルは chat_ctx を途中更新できない — 次ターンで反映)")
            return
        if not session.output.audio_enabled:
            logger.info("耳打ち即時応答なし (operator応対中 — AIは聞き役)")
            return
        if session.user_state == "speaking":
            logger.info("耳打ち即時応答なし (相手の発話中 — このターンの返事に反映)")
            return
        try:
            session.generate_reply(
                instructions=(
                    "たった今、本人からの耳打ちが届いた。会話の流れを壊さずに、"
                    "耳打ちを踏まえて相手に今言うべきことだけを短く言う。"
                    "耳打ちの存在や内容は相手に明かさない"
                ),
            )
            logger.info("耳打ちへ即時応答を生成")
        except Exception:
            logger.exception("耳打ちの即時応答に失敗 (次ターンで反映される)")

    # ②後方支援メモ + 耳打ちの配達 (通話終了 = shutdown で自然に止まる)。
    # NOTIFY (agent_push) で即起床し、保険として2秒ごとの巡回も残す。
    # どちらも inject_chat_text で会話履歴に差し込む (相手に音声は出ない・時系列が残る)
    push_event = asyncio.Event()

    async def watch_agent_notes():
        await db.listen_push(push_event.set)
        last_note_id = 0
        last_whisper_id = 0
        while True:
            try:
                await asyncio.wait_for(push_event.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            push_event.clear()
            try:
                notes = await db.fetch_new_notes(last_note_id)
                whispers = await db.fetch_new_whispers(last_whisper_id)
            except Exception:
                return  # pool close後 (通話終了) は静かに終わる
            for n in notes:
                last_note_id = n["id"]
                await inject_chat_text(
                    "後方支援メモ・裏方エージェントより (相手には聞こえていない参考情報)",
                    n["result"],
                )
                logger.info("agent_note injected: %s", n["result"][:80])
            for w in whispers:
                last_whisper_id = w["id"]
                writer.add_segment("whisper", w["text"], False)  # JSONLバックアップ
                await inject_chat_text(
                    "耳打ち・本人より (相手には聞こえていない。存在を明かさず応対に反映)",
                    w["text"],
                )
                logger.info("耳打ちをchat_ctxへ注入: %s", w["text"][:80])
            if whispers:
                _reply_to_whisper()
            # AI 応答の切り替え (端末の「AIに任せる」等) も agent_push で届く
            ai_on = await db.get_ai_on()
            if ai_on is not None and ai_on != ctl["ai_on"]:
                ctl["ai_on"] = ai_on
                logger.info("AI 応答 → %s (端末から)", "オン" if ai_on else "オフ")
                _kick("ai_on")

    t_ctx_inject = asyncio.create_task(inject_caller_context())
    t_notes_watch = asyncio.create_task(watch_agent_notes())

    def _cancel_bg_tasks():
        t_ctx_inject.cancel()
        t_notes_watch.cancel()
        stt_tracker.stop_all()

    ctx.add_shutdown_callback(_cancel_bg_tasks)

    def _talkers() -> list[rtc.RemoteParticipant]:
        return [p for p in ctx.room.remote_participants.values() if p.identity.startswith("operator-")]

    def _presence() -> list[dict]:
        out = []
        for p in ctx.room.remote_participants.values():
            role = (
                "talk" if p.identity.startswith("operator-")
                else "watch" if p.identity.startswith(("watch-", "dashboard-"))
                else None
            )
            if role:
                out.append({"id": p.identity.split("-", 1)[1], "name": p.name or "", "role": role})
        return out

    def _decide() -> str:
        if _talkers():
            return "human"
        if ctl["ai_on"]:
            return "ai"
        if not ctl["ever_talker"] and asyncio.get_running_loop().time() - ctl["started"] < CONNECT_GRACE_SEC:
            return "connecting"
        return "hold"

    async def _music(on: bool) -> None:
        if not on:
            if ctl["music"] is not None:
                ctl["music"].stop()
                ctl["music"] = None
            return
        if ctl["music"] is not None:
            return
        try:
            if ctl["player"] is None:
                # ⚠最初の保留のときに作る。作るとルームに音声トラックが 1 本増える
                player = BackgroundAudioPlayer()
                await player.start(room=ctx.room)
                ctl["player"] = player
            await _load_hold_music()
            ctl["music"] = ctl["player"].play(AudioConfig(_hold_music_loop(), volume=0.6))
        except Exception:
            logger.exception("保留音を流せない (保留は無音で続く)")

    async def _hold_timeout() -> None:
        await asyncio.sleep(HOLD_TIMEOUT_SEC)
        ctl["hold_timer"] = None  # ⚠_apply が自分を cancel しないように先に外す
        if (
            ctl["handler"] != "hold"
            or ctl["apply"] is None
            or ctx.room.connection_state != rtc.ConnectionState.CONN_CONNECTED
        ):
            return
        # ⚠「自分で出る」の間は AI が引き取らない (2026-10-08 ユーザー)。保留のまま見張り直し、
        #   モードが変わっていたら次の区切りで引き取る。「AIに任せる」を押したときは従来どおり AI が出る
        if await _answer_mode() == "manual":
            logger.info("保留が %.0f 秒続いたが自分で出るモード — AI は引き取らない", HOLD_TIMEOUT_SEC)
            if ctl["handler"] == "hold" and ctl["hold_timer"] is None:
                ctl["hold_timer"] = asyncio.create_task(_hold_timeout())
            return
        logger.info("保留が %.0f 秒続いた — AI が引き取る", HOLD_TIMEOUT_SEC)
        ctl["ai_on"] = True
        await db.set_ai_on(True)
        await _apply("hold_timeout")

    async def _apply(reason: str) -> None:
        if (
            ctl["apply"] is None
            or (getattr(ctx, "_closed", None) is not None and ctx._closed.is_set())
            or ctx.room.connection_state != rtc.ConnectionState.CONN_CONNECTED
        ):
            return
        async with ctl_lock:
            if (
                ctl["apply"] is None
                or (getattr(ctx, "_closed", None) is not None and ctx._closed.is_set())
                or ctx.room.connection_state != rtc.ConnectionState.CONN_CONNECTED
            ):
                return
            stt_tracker.sync_tracks(participant.identity)
            new = _decide()
            old = ctl["handler"]
            await db.set_handler(new, _presence())
            if new == old:
                return
            ctl["handler"] = new
            logger.info("担い手: %s → %s (%s)", old, new, reason)

            if new == "hold":
                await _music(True)
                if ctl["hold_timer"] is None:
                    ctl["hold_timer"] = asyncio.create_task(_hold_timeout())
            else:
                await _music(False)
                if ctl["hold_timer"] is not None:
                    ctl["hold_timer"].cancel()
                    ctl["hold_timer"] = None

            if new == "human":
                ctl["ever_talker"] = True
                try:
                    session.interrupt()
                except Exception:
                    pass
                session.output.set_audio_enabled(False)
                if ctl["ai_on"]:
                    ctl["ai_on"] = False
                    await db.set_ai_on(False)
                if db.call_id is not None and db._pool:
                    # 本人が受けた通話なら最初から人間が応答した通話
                    by = "human" if scribe_mode else "ai_then_human"
                    asyncio.create_task(
                        db._pool.execute(
                            "UPDATE calls SET answered_by=$2 WHERE id=$1 AND answered_by <> 'human'",
                            db.call_id, by,
                        )
                    )
            elif new == "ai":
                session.output.set_audio_enabled(True)
                if old is None:
                    return  # 通話の始まり。挨拶は下の GREETING
                if old == "human":
                    await inject_chat_text(
                        "状況 (相手には聞こえていない)",
                        "ここまで本人が相手と話していた。ここからはあなたが応対を引き継ぐ。",
                    )
                line = (
                    HOLD_TIMEOUT_LINE if reason == "hold_timeout"
                    else None if ctl["spoke"]
                    else TAKEOVER_LINE
                )
                if line:
                    ctl["spoke"] = True
                    speak_fixed(line, allow_interruptions=False)
            else:  # hold / connecting — AI は黙る
                try:
                    session.interrupt()
                except Exception:
                    pass
                session.output.set_audio_enabled(False)
                if new == "connecting":
                    asyncio.get_running_loop().call_later(
                        CONNECT_GRACE_SEC + 0.5, _kick, "connect_grace"
                    )

    async def _stop_player():
        # ⚠保留中に通話が終わったとき、タイマーと保留音を先に止める。止めずに aclose を待つと
        #   ジョブの終了が 10 秒の期限に間に合わず強制終了になった (2026-09-26 実測)
        ctl["apply"] = None
        if ctl["hold_timer"] is not None:
            ctl["hold_timer"].cancel()
            ctl["hold_timer"] = None
        if ctl["music"] is not None:
            ctl["music"].stop()
            ctl["music"] = None
        player = ctl["player"]
        ctl["player"] = None
        if player is not None:
            try:
                await asyncio.wait_for(player.aclose(), 3)
            except Exception:
                logger.info("保留音の片付けが間に合わなかった (そのまま終える)")

    ctx.add_shutdown_callback(_stop_player)
    asyncio.create_task(_load_hold_music())
    ctl["apply"] = _apply
    await db.set_ai_on(ctl["ai_on"])
    await _apply("start")

    if scribe_mode:
        return  # 挨拶もしない (本人が話す)
    if is_reentry:
        # 再入室モード (calld 再起動や瞬断からの自動復旧): 挨拶は繰り返さず、DB の会話記録を注入して詫びる
        history = await db.fetch_transcript_history()
        if history:
            lines = [
                f"{'相手' if h['speaker'] == 'caller' else 'AI/本人'}: {h['text']}"
                for h in history
            ]
            await inject_chat_text(
                "再入室時の会話履歴復元 (相手には聞こえていない。ここまでの流れを踏まえて続きを聞くこと)",
                "\n".join(lines),
            )
        logger.info("BENCH T4_greeting room=%s reentry=True", ctx.room.name)
        await speak_fixed(
            "失礼いたしました、お電話が少々遠くなっておりました。続きをお伺いいたします。",
            allow_interruptions=False,
        )
        return
    # 受話直後に一拍おく (人間らしい間 + 管制室モニタの接続が挨拶の頭に間に合いやすくなる)
    await asyncio.sleep(0.5)
    # 固定挨拶は say() で確定再生。generate_reply は gemini-3.1-flash-live-preview 非対応で
    # 毎回エラーになり「相手のもしもしが先」の真因だった (2026-07-17実機ログで特定)
    #
    # ⚠allow_interruptions=False が要る (2026-07-30の実機テストで判明)。
    # 既定は割り込み可なので、相手が喋り続けるとAIは名乗りもできないまま黙る。
    # 実際に「AIが一言も発せないまま通話終了」が起きた。ここだけは相手より優先させる —
    # 名乗りと録音告知は「言えなかった」で済ませられない性質のものなので。
    # 代わりに GREETING を約3秒に詰めてある (長い文を割り込み無効で流すと無礼になる)
    logger.info("BENCH T4_greeting room=%s reentry=False", ctx.room.name)
    await speak_fixed(greeting(call_self_label), allow_interruptions=False)


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if os.environ.get("AGENT_MODE") == "http" or (len(sys.argv) > 1 and sys.argv[1] == "http"):
        run_http_server(entrypoint)
    elif os.environ.get("CLOUD_AGENT_URL", "").strip():
        logger.error(
            "CLOUD_AGENT_URL is set (%s) while AGENT_MODE=worker — "
            "refusing to start local LiveKit worker to prevent duplicate room entry.",
            os.environ.get("CLOUD_AGENT_URL"),
        )
        sys.exit(0)
    else:
        # job_memory_warn_mb=0: 通話プロセスのメモリ監視を止める (2026-10-02)。警告ログを出すだけの機能で、
        #   5 秒ごとに全プロセスの smaps を読む。VM の py-spy で通話中 CPU の約 4% がこれだった
        #   (上限 job_memory_limit_mb は元々 0 = なし)
        # NUM_IDLE_PROCESSES: 着信に備えて先に立ち上げておく通話プロセスの数 (2026-10-02)。
        #   既定はライブラリの既定 (本番は CPU 数 = e2-medium で 2)。1 つ約 380MB なので、
        #   VM を 2GB に下げるなら 1 にする。2 本目の同時着信はプロセスの立ち上げを待つ
        opts: dict = {"entrypoint_fnc": entrypoint, "job_memory_warn_mb": 0}
        if os.environ.get("NUM_IDLE_PROCESSES"):
            opts["num_idle_processes"] = int(os.environ["NUM_IDLE_PROCESSES"])
        cli.run_app(WorkerOptions(**opts))
