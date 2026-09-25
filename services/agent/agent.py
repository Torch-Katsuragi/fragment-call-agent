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
from itertools import count
from pathlib import Path

import asyncpg
from google.cloud import texttospeech as gcloud_texttospeech
from google.genai import types as genai_types
from livekit import api as lk_api
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentSession,
    AutoSubscribe,
    ConversationItemAddedEvent,
    JobContext,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
)
from livekit.agents import metrics as lk_metrics
from livekit.agents import stt as lk_stt
from livekit.agents.voice.room_io import RoomInputOptions
from livekit.agents.llm import ChatMessage
from livekit.plugins import deepgram, google, silero

import security
from aivis_tts import DEFAULT_MODEL_UUID as AIVIS_DEFAULT_MODEL
from aivis_tts import AivisTTS
from livekit.agents import tts as tts_mod
from voicevox_tts import VoicevoxTTS

logger = logging.getLogger("call-agent")

TRANSCRIPT_DIR = Path(os.environ.get("TRANSCRIPT_DIR", "/data/transcripts"))
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# ユーザー編集可能なシステムプロンプトは M4 以降 (DESIGN §5.3)。M2 は固定文で検証する。
JST = timezone(timedelta(hours=9))


def _now_jst_str() -> str:
    now = datetime.now(JST)
    return f"{now:%Y-%m-%d}({'月火水木金土日'[now.weekday()]}) {now:%H:%M}"


# 方針: モデルに自分の状況を正直に伝える (2026-07-16 実機テストで「存在しない取り次ぎを
# 約束して黙り込む」症状 → 状況を隠さず説明する方が誤った約束をしなくなる)
# ===== 編集可能なプロンプトのセクション (2026-07-30に外出し) =====
# なぜ外に出したか: 口調・態度・「誰に何を教えるか」は「実機で聞いてみて直す」ものなので、
# 直すたびにコンテナを再ビルドするのは重すぎた (この日だけで人格の調整が4回入った)。
# mdでワークスペース(=Google Drive)に置けば、管制室の設定画面からもObsidianからも直せる。
#
# セクションに切って**合成**する形にした理由 (ユーザーの案):
#   「個人情報の扱い」と「人格・態度」は別の関心事で、直す動機も頻度も違う。
#   1枚の巨大プロンプトだと、口調を直したいだけのときに開示ルールまで目に入って事故る。
# ⚠**開示ルールも編集できる**。当初これをコードに固定したが、「誰に何を教えるか」は
#   運用する本人が調整したい部分なので誤りだった (ユーザーの指摘で撤回)。
#   代わりに、消したら落ちる項目は自動テストで見る (run_scenarios.py の no_owner_name 等)。
#
# 「あなたの状況」「あなたの目的」「応対のしかた」は仕組みの説明とツールの使い方なので固定。
PROMPTS_DIR = Path(__file__).parent / "prompts"

# key: {既定ファイル, ワークスペース上のファイル名, 差し込み先}
# ⚠ダッシュボード側 (app/api/prompts/route.ts) と揃えること。ズレると編集先が食い違う
PROMPT_SECTIONS: dict[str, dict[str, str]] = {
    "disclosure": {
        "title": "情報の扱い (開示ルール)",
        "default": "disclosure.md",
        "workspace": "設定/情報の扱い.md",
    },
    "persona": {
        "title": "人格と態度 (話し方・温度感)",
        "default": "persona.md",
        "workspace": "設定/人格と態度.md",
    },
    # ⚠これだけ性質が違う: 「設定」ではなく**頻繁に変わる状態**。
    #   判断には使うが相手には明かさない (非対称)。更新を忘れると嘘になるので、
    #   読み込み時に最終更新日時を添えてモデルに渡す (下の load_prompt_section 参照)
    "owner_status": {
        "title": "本人の今の状況 (相手には明かさない)",
        "default": "owner_status.md",
        "workspace": "設定/本人の今の状況.md",
    },
}


OWNER_NAME = os.environ.get("OWNER_NAME", "").strip()

# 無言検知のしきい値 (2026-07-31)。電話で5秒の沈黙は長い — 相手が黙ったら一度促し、
# それでも黙っていたら切る。⚠1回で切らないのは、電波が悪いだけの人や機械が苦手で
# 言葉に詰まる人を切ってしまうため
SILENCE_SEC = float(os.environ.get("SILENCE_SEC") or "5")


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

def _urgent_allowed(number: str) -> bool:
    """この相手は緊急呼び出し (本人の携帯を鳴らすこと) を許可されているか。
    実体はワークスペースの電話帳md `連絡先/<番号>.md` の `緊急呼び出し` プロパティで、
    **本人が管制室で承認したときだけ** true になる。

    ⚠番号や名乗りから推測してはいけない。日本では発信者番号の偽装が現在進行形の主要手口で、
      警察庁が「表示された番号を信用するな」と公式に注意喚起している (末尾0110の警察署番号を
      騙る詐欺・被害100億円超)。着信側で検証する基盤が日本には無いので、
      信用できるのは本人の明示的な承認だけ。⚠非通知・+始まりは常に不許可
      (キャリアが偽装を潰した結果がその形なので、むしろ疑わしい印)。
    """
    if not re.fullmatch(r"[0-9]{4,20}", number or ""):
        return False
    ws = os.environ.get("FRAGMENT_WORKSPACE", "")
    if not ws:
        return False
    try:
        text = (Path(ws) / "連絡先" / f"{number}.md").read_text(encoding="utf-8")
    except Exception:
        return False
    m = re.search(r"^緊急呼び出し:\s*(\S+)\s*$", text, re.M)
    return bool(m) and m.group(1).strip().lower() in ("true", "yes", "はい")


def load_prompt_section(key: str) -> str:
    """プロンプトのセクションを読む。ワークスペースにあればそれ、無ければリポジトリの既定。
    通話ごとに読むので、mdを直せば次の通話から効く (再起動不要)。"""
    spec = PROMPT_SECTIONS[key]
    ws = os.environ.get("FRAGMENT_WORKSPACE", "")
    if ws:
        p = Path(ws) / spec["workspace"]
        try:
            if p.is_file():
                text = p.read_text(encoding="utf-8").strip()
                if text:
                    # ⚠「本人の今の状況」は放置されると嘘になる (「会議中」のまま1週間、など)。
                    #   最終更新を添えると、モデルが現在日時と見比べて古さを割り引ける
                    if key == "owner_status":
                        at = datetime.fromtimestamp(p.stat().st_mtime, JST)
                        text += (
                            f"\n\n(この状況が最後に更新されたのは {at:%Y-%m-%d %H:%M} です。"
                            "現在日時と離れているほど古い情報として扱い、"
                            "古ければ状況を当てにせず、通常どおり用件を預かること)"
                        )
                    return text
                logger.warning("プロンプト %s が空 (%s) — 既定を使う", key, p)
        except Exception:
            logger.exception("プロンプト %s を読めない (%s) — 既定を使う", key, p)
    try:
        return (PROMPTS_DIR / spec["default"]).read_text(encoding="utf-8").strip()
    except Exception:
        logger.exception("既定のプロンプト %s も読めない — このセクション無しで続行", key)
        return ""


def build_instructions(caller: str | None) -> str:
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
    s_disclosure = load_prompt_section("disclosure")
    s_persona = load_prompt_section("persona")
    return f"""\
あなたは、この電話の持ち主に代わって着信へ出るAIアシスタントです。
相手の言語は日本語を想定して応対します (聞き取りが不確かでも、まず日本語で返す)。
立ち位置は「インタラクティブな留守電」— 用件を預かるのが仕事で、相槌と短い聞き返しが
できる点だけが普通の留守電と違う。AIが先に用件を伺う係であることは、聞かれたら
正直に答えてよい (プロダクト名は名乗らない)。

{s_disclosure}

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
- end_call: 用件が済んだら「確かにお伝えしておきます。失礼いたします」と手短に締め、
  **同じ応答の中で必ず呼ぶ**。呼ばない限り回線は切れず、相手は無音で待たされる
これ以外の操作 (転送・保留・調べもの) は、あなたの手元にはない。

# 目的
- ゴールは用件を「聞き出して」記録に残すこと。解決は持ち主の仕事 — 記録を読み、
  準備して折り返す。取るのは3つ: **誰が / 何の用件で / どうしてほしいか**
- 話のイニシアチブは相手に握らせる。言いたいことを言わせる過程で自然に引き出す
- **あなたは約束をしない**。あなたの約束は、持ち主が守る羽目になる。口にしてよいのは
  事実だけ:「承りました」「お伝えしておきます」。折り返しや期限は、相手が希望した時に
  「ご希望としてお伝えしておきます」と希望のまま預かる (こちらから持ちかけない)
- 事前情報・【後方支援メモ】・過去の記録は参考情報。今の用件と同じと決めつけず、
  迷ったら相手に短く確認する。そこに無いことは知らないことなので、調べる素振りをせず
  「分かりかねますので、確認いたします」と預かる

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

# 関数呼び出しが本文に溢れた印。⚠2026-08-01に gemini-3.6-flash で実測した実際の文字列から。
# 表記が毎回違う (`#CALL:` `[call:` `<call:` `Address:` の前置き、BOMやデーヴァナーガリーの混入) ので、
# **共通して残る `default_api:` と `end_call{` を拾う**。これが本文に出た回はツールが
# 呼ばれていないことがあり、そのときは切るつもりで切れていない
TOOL_LEAK = re.compile(r"default_api:|end_call\s*\{|request_handoff\s*\{|<call:|#CALL:")


class TranscriptWriter:
    """生ログのJSONLバックアップ。通話1件 = 1ファイル、発信者番号ごとのディレクトリ。"""

    def __init__(self, room_name: str, caller: str | None):
        started = datetime.now(timezone.utc)
        safe_caller = re.sub(r"[^0-9A-Za-z+]", "_", caller or "unknown")
        d = TRANSCRIPT_DIR / safe_caller
        d.mkdir(parents=True, exist_ok=True)
        safe_room = re.sub(r"[^0-9A-Za-z_-]", "_", room_name)
        self.path = d / f"{started.strftime('%Y%m%dT%H%M%SZ')}_{safe_room}.jsonl"
        self._write(
            {
                "type": "meta",
                "event": "call_start",
                "room": room_name,
                "caller": caller,
                "at": started.isoformat(),
            }
        )
        logger.info("transcript -> %s", self.path)

    def _write(self, obj: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def add_segment(self, speaker: str, text: str, interrupted: bool = False) -> None:
        self._write(
            {
                "type": "segment",
                "speaker": speaker,  # caller(=発信者) / ai
                "text": text,
                "interrupted": interrupted,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def close(self) -> None:
        self._write(
            {"type": "meta", "event": "call_end", "at": datetime.now(timezone.utc).isoformat()}
        )


class CallDb:
    """通話履歴DB (PostgreSQL)。スキーマは infra/postgres/init.sql。
    DB障害で通話を落とさないよう、全操作はエラーを握りつぶしてログに残すだけにする。"""

    def __init__(self):
        self._pool: asyncpg.Pool | None = None
        self._listen_conn: asyncpg.Connection | None = None
        self.call_id = None
        self._seq = count(1)

    async def start_call(self, room: str, caller: str | None) -> None:
        try:
            self._pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3)
            row = await self._pool.fetchrow(
                """INSERT INTO calls (room_name, caller_number)
                   VALUES ($1, $2)
                   ON CONFLICT (room_name) DO UPDATE SET caller_number = EXCLUDED.caller_number
                   RETURNING id""",
                room,
                caller,
            )
            self.call_id = row["id"]
            logger.info("call row created: %s", self.call_id)
        except Exception:
            logger.exception("CallDb.start_call failed (通話は継続)")

    async def update_caller(self, caller: str) -> None:
        if not self._pool or self.call_id is None:
            return
        try:
            await self._pool.execute(
                "UPDATE calls SET caller_number = $2 WHERE id = $1", self.call_id, caller
            )
        except Exception:
            logger.exception("CallDb.update_caller failed")

    async def add_segment(self, speaker: str, text: str, interrupted: bool) -> None:
        if not self._pool or self.call_id is None:
            return
        try:
            await self._pool.execute(
                """INSERT INTO transcript_segments (call_id, seq, speaker, text, interrupted)
                   VALUES ($1, $2, $3, $4, $5)""",
                self.call_id,
                next(self._seq),
                speaker,
                text,
                interrupted,
            )
        except Exception:
            logger.exception("CallDb.add_segment failed")

    async def search(self, caller: str | None, query: str) -> list[str]:
        if not self._pool:
            return []
        rows = await self._pool.fetch(
            """SELECT c.started_at, ts.speaker, ts.text
               FROM transcript_segments ts JOIN calls c ON c.id = ts.call_id
               WHERE ts.text ILIKE '%' || $1 || '%'
                 AND ($2::text IS NULL OR c.caller_number = $2)
                 AND c.id <> $3
               ORDER BY ts.at DESC LIMIT 10""",
            query,
            caller,
            self.call_id,
        )
        return [
            f"[{r['started_at']:%Y-%m-%d %H:%M}] {r['speaker']}: {r['text']}"
            for r in reversed(rows)
        ]

    async def create_job(self, kind: str, query: str) -> int | None:
        """Directory Agent (別プロセスのworker) へのジョブ投入。"""
        if not self._pool:
            return None
        row = await self._pool.fetchrow(
            "INSERT INTO agent_jobs (call_id, kind, query) VALUES ($1, $2, $3) RETURNING id",
            self.call_id,
            kind,
            query,
        )
        return row["id"]

    async def get_job(self, job_id: int) -> tuple[str, str | None]:
        row = await self._pool.fetchrow(
            "SELECT status, result FROM agent_jobs WHERE id = $1", job_id
        )
        return (row["status"], row["result"]) if row else ("error", None)

    async def find_done_job(self, query: str) -> str | None:
        """同一通話内で同じ質問が完了済みならその結果を返す (タイムアウト後の再問い合わせ用)。"""
        if not self._pool or self.call_id is None:
            return None
        row = await self._pool.fetchrow(
            """SELECT result FROM agent_jobs
               WHERE call_id = $1 AND query = $2 AND status = 'done'
               ORDER BY id DESC LIMIT 1""",
            self.call_id,
            query,
        )
        return row["result"] if row else None

    async def find_recent_done_job(self, query: str) -> str | None:
        """直近3分以内の完了済みジョブを call_id 不問で探す。

        着信フック (hookd) 経由の下調べジョブは INVITE 時点で投入されるため
        call_id を持たない — 入室後のagentはこれで拾う。"""
        if not self._pool:
            return None
        row = await self._pool.fetchrow(
            """SELECT result FROM agent_jobs
               WHERE query = $1 AND status = 'done'
                 AND finished_at > now() - interval '3 minutes'
               ORDER BY id DESC LIMIT 1""",
            query,
        )
        return row["result"] if row else None

    async def fetch_new_whispers(self, after_id: int) -> list:
        """管制室が会話ストリーム (transcript_segments) に直接挿入した耳打ちの新着。
        会話ストリームがバス — ダッシュボードが書き、UI/watcher/アシスタントが購読する。"""
        if not self._pool or self.call_id is None:
            return []
        return await self._pool.fetch(
            """SELECT id, text FROM transcript_segments
               WHERE call_id = $1 AND speaker = 'whisper' AND id > $2 ORDER BY id""",
            self.call_id,
            after_id,
        )

    async def listen_push(self, on_push) -> None:
        """agent_push チャネルの LISTEN (payload = call_id)。耳打ち・後方支援メモを
        NOTIFY で即時配達する — 2秒ポーリングの待ちを潰す (ポーリングは保険で継続)。
        LISTEN はコネクション単位なので専用の直結コネクションを1本持つ。"""
        if self.call_id is None:
            return
        try:
            self._listen_conn = await asyncpg.connect(DATABASE_URL)

            def _notified(conn, pid, channel, payload):
                if payload == str(self.call_id):
                    on_push()

            await self._listen_conn.add_listener("agent_push", _notified)
        except Exception:
            logger.exception("listen_push failed (2秒ポーリングで動作継続)")

    async def outbound_intent(self, number: str) -> bool:
        """発信の印 (hookd /dial が置く)。この番号への直近の発信通話ならTrue (2026-07-19)。"""
        if not self._pool:
            return False
        try:
            row = await self._pool.fetchrow(
                """SELECT 1 FROM agent_jobs
                   WHERE kind = 'outbound_call' AND query = $1
                     AND created_at > now() - interval '3 minutes' LIMIT 1""",
                number,
            )
            return row is not None
        except Exception:
            logger.exception("CallDb.outbound_intent failed")
            return False

    async def mark_outbound(self, callee: str) -> None:
        if not self._pool or self.call_id is None:
            return
        try:
            await self._pool.execute(
                """UPDATE calls SET direction = 'outbound', callee_number = $2,
                       answered_by = 'human' WHERE id = $1""",
                self.call_id,
                callee,
            )
        except Exception:
            logger.exception("CallDb.mark_outbound failed")

    async def assistant_enabled(self) -> bool:
        """留守電トグル。false でも通話がスタックを通る限り書記 (文字起こし+吹き出し) は動く —
        このフラグが制御するのは「AIが喋るかどうか」だけ (支援機能とは独立、2026-07-18設計)。"""
        if not self._pool:
            return True
        row = await self._pool.fetchrow(
            "SELECT value FROM settings WHERE key = 'assistant_enabled'"
        )
        return row is None or row["value"] != "false"

    async def tts_providers(self) -> tuple[str | None, str | None]:
        """管制室の設定画面で選べる声の「メイン / サブ」。settings テーブルから読む。
        通話ごとに読むので、切り替えは**次の通話から**効く (コンテナ再起動は不要)。
        行が無ければ (None, None) を返し、呼び出し側が環境変数の既定にフォールバックする —
        既存DBにマイグレーションを当てなくても動くようにするため。"""
        if not self._pool:
            return None, None
        try:
            rows = await self._pool.fetch(
                "SELECT key, value FROM settings WHERE key IN ('tts_primary', 'tts_fallback')"
            )
            got = {r["key"]: (r["value"] or "").strip() for r in rows}
            return got.get("tts_primary") or None, got.get("tts_fallback") or None
        except Exception:
            logger.exception("CallDb.tts_providers failed — 環境変数の既定を使う")
            return None, None

    async def fetch_new_notes(self, after_id: int) -> list:
        """後方支援メモ (workerが会話を監視して自発的に投入する agent_note) の新着を取る。"""
        if not self._pool or self.call_id is None:
            return []
        return await self._pool.fetch(
            """SELECT id, result FROM agent_jobs
               WHERE call_id = $1 AND kind = 'agent_note' AND status = 'done' AND id > $2
               ORDER BY id""",
            self.call_id,
            after_id,
        )

    async def security_check(self, caller: str | None, callee: str | None):
        """SIP不正利用の判定 (security.py)。Asterisk経由なら hookd /guard で既に落ちている —
        ここに来るのは**FWやtrunkのallowed_addressesをすり抜けて直接LiveKitに入った呼**で、
        2026-07-26に実際に起きた経路。判定はセッション生成前なのでSTT/TTS/LLMは1トークンも使わない。
        DB不調時は None を返す = 通話を通す (警備が着信を止める方が損害が大きい)。"""
        if not self._pool:
            return None
        try:
            await security.ensure_schema(self._pool)
            return await security.evaluate(self._pool, caller, callee, source="agent")
        except Exception:
            logger.exception("security_check failed (通話は継続 — fail-open)")
            return None

    async def drop_blocked_call(self) -> None:
        """ブロックした呼の通話行を消す。証跡は security_events / call_attempts が持つので、
        履歴・電話帳・通話記録mdを攻撃で汚さない (2026-07-26は108件の手作業掃除が発生した)。"""
        if not self._pool or self.call_id is None:
            return
        try:
            await self._pool.execute("DELETE FROM calls WHERE id = $1", self.call_id)
            self.call_id = None
        except Exception:
            logger.exception("CallDb.drop_blocked_call failed")

    async def end_call(self) -> None:
        conn, self._listen_conn = self._listen_conn, None
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass
        if not self._pool:
            return
        try:
            if self.call_id is not None:
                await self._pool.execute(
                    "UPDATE calls SET ended_at = now() WHERE id = $1", self.call_id
                )
        except Exception:
            logger.exception("CallDb.end_call failed")
        finally:
            pool, self._pool = self._pool, None  # 冪等化 (close経路とshutdown経路の二重呼び対策)
            await pool.close()


class PhoneAgent(Agent):
    """電話応答エージェント。過去通話の検索と切電のツールを持つ。"""

    def __init__(self, db: CallDb, caller: str | None, job_ctx: JobContext):
        super().__init__(instructions=build_instructions(caller))
        self._db = db
        self._caller = caller

        self._job_ctx = job_ctx

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
        logger.info("end_call invoked by AI")
        try:
            await context.wait_for_playout()  # 別れの挨拶を言い終えるまで待つ
        except Exception:
            pass
        await self._job_ctx.api.room.delete_room(
            lk_api.DeleteRoomRequest(room=self._job_ctx.room.name)
        )

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
        logger.info("request_handoff invoked by AI: %s", reason[:80])
        # ⚠**鳴らしてよい相手かはコードで決める。** プロンプトに条件を書いても確率的に破られる
        #   (LLMに任せるのは「用件が緊急か」まで)。許可は本人が管制室で与えたものだけを信用する —
        #   発信者番号は偽装され得るので、番号や名乗りからは推測しない (2026-08-01の調査)
        if not _urgent_allowed(self._caller or ""):
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


def _make_stt():
    """STTは STT_PROVIDER 環境変数で切り替え (デフォルト: deepgram)。

    - deepgram: nova-3。確定が速い (0.3〜0.4s) が、日本語の電話音声 (8kHz) で誤認識が目立つ
                (2026-07-18実機:「この後どうすか」→「この後道どうすか。ばあたり。」)
    - google:   Cloud Speech-to-Text v2 ストリーミング。認証はCloud TTSと同じSA鍵。
                モデルは GOOGLE_STT_MODEL で切替 (latest_long / telephony / chirp_2 等。
                chirp系はストリーミング対応リージョンが限られる → GOOGLE_STT_LOCATION)
    """
    if os.environ.get("STT_PROVIDER", "deepgram") == "google":
        # ⚠既定は0 =「speech_end_timeout を指定しない」(Google任せ)。0以外で明示指定になる。
        #
        # ⚠**この値を入れると会話が1往復で死ぬ** (2026-07-31にA/Bで確定)。
        #   0.8を指定すると相手の**1発話目しか認識されず**、以降は無言扱いになって
        #   無言検知の切断まで進む。同じシナリオで実測: 0.8=7発話/81秒(1往復で死亡) /
        #   未指定=15発話/123秒(正常)。Googleが無音検知でストリームを閉じ、
        #   張り直されないためと見られる。
        #
        # ⚠そもそもこれを入れた動機 (「無音を挟んだ2発話が1つの17秒発話にまとめられる」) は、
        #   **GCPファイアウォールのRTPポート不一致で相手の音声が虫食いで届いていた**のが原因
        #   だった (許可 udp:10000-10500 に対し Asterisk の実使用は 20000-20500)。
        #   音が飛び飛びなら発話の切れ目が見つからないのは当然で、STTの設定は無実だった。
        #   回線を直した後は未指定でも まとめ吐きは再現しない。**症状の原因を1つ下の層で
        #   取り違えた例**として残す — 直す前に、その層まで音が届いているかを先に測ること。
        end_timeout = float(os.environ.get("GOOGLE_STT_END_TIMEOUT") or "0")
        extra = {"speech_end_timeout": end_timeout} if end_timeout > 0 else {}
        return google.STT(
            languages="ja-JP",
            detect_language=False,
            model=os.environ.get("GOOGLE_STT_MODEL", "latest_long"),
            location=os.environ.get("GOOGLE_STT_LOCATION", "global"),
            **extra,
            # speech_end_timeout は既定で渡さない (経緯は上の end_timeout の注記)
            credentials_file=os.environ.get(
                "GOOGLE_TTS_CREDENTIALS", "/secrets/callagent-tts.json"
            ),
        )
    return deepgram.STT(model="nova-3", language="ja")


def _make_one_tts(provider: str):
    """プロバイダ名1つからTTSを作る (フォールバックの組み立ては _make_tts 側でやる)。"""
    if provider == "aivis":
        # Aivis Cloud API (声=コハク)。初回バイト0.08〜0.12秒の実測 → 詳細は aivis_tts.py
        return AivisTTS(
            api_key=os.environ.get("AIVIS_API_KEY", ""),
            # ⚠get()の既定値ではなく or を使う。composeが `${VAR:-}` で**空文字**を
            #   渡してくるため、キーは存在してしまい既定値が効かない (2026-07-30)
            model_uuid=os.environ.get("AIVIS_MODEL_UUID") or AIVIS_DEFAULT_MODEL,
            speaking_rate=float(os.environ.get("AIVIS_SPEAKING_RATE") or "1.2"),
            # 既定2.0。1.0だと電話で「声が小さい」= Googleより8dB低かった (aivis_tts.py参照)
            volume=float(os.environ.get("AIVIS_VOLUME") or "2.0"),
        )
    if provider == "google":
        return _make_google_tts()
    if provider == "elevenlabs":
        # 日本語の漢字読み間違いが多く不採用 (2026-07-18)。コードは切替可能なまま残置
        from livekit.plugins import elevenlabs

        return elevenlabs.TTS(model="eleven_flash_v2_5", language="ja")
    if provider == "voicevox":
        return VoicevoxTTS(
            base_url=os.environ.get("VOICEVOX_URL", "http://voicevox:50021"),
            speaker=int(os.environ.get("VOICEVOX_SPEAKER", "2")),
        )
    # ⚠3.8 は livekit-plugins-google 1.8.3 以上が要る (1.8.2 だと instructions を読み上げる。
    #   requirements.txt 参照)。GEMINI_TTS_MODEL で戻せる
    return google.beta.GeminiTTS(
        model=os.environ.get("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts"),
        voice_name=os.environ.get("GEMINI_VOICE", "Leda"),
        instructions="電話の受付担当として、丁寧で自然な日本語で話してください。",
    )


# 管制室の設定画面に出す選択肢。ここに無い値が来たら gemini 扱いになる
TTS_CHOICES = ("aivis", "google", "gemini", "elevenlabs", "voicevox")


def _make_tts(primary: str | None = None, fallback: str | None = None):
    """メイン/サブの2段構えでTTSを作る。

    引数は管制室の設定 (settingsテーブル) から渡される。None なら環境変数の既定を使う。
    ⚠**サブを置く理由**: 外部APIに声を預けると、落ちたときに電話が無言になる。
      通話で一番痛い障害がそれなので、合成が失敗したら FallbackAdapter が次に切り替える。
      サブ無し (None/same) でも動くが、その場合は無言になるリスクを受け入れることになる。

    - google:   Google Cloud TTS StreamingSynthesize (Chirp 3: HD)。真のストリーミングで
                初動が速く、日本語の読み(漢字)もGoogle品質。認証はサービスアカウント鍵
                (GOOGLE_TTS_CREDENTIALS、GOOGLE_API_KEYでは動かない)
    - gemini:   Gemini TTS (gemini-3.8-flash-tts)。既存のGOOGLE_API_KEYで動く。
                声は GEMINI_TTS_VOICE (Live APIと同じ30声: Leda/Kore/Aoede/Zephyr等)。
                非ストリーミングで初動1〜2秒
    - voicevox: ローカルのVOICEVOX ENGINE (compose --profile voicevox で起動)。
                無料だがCPU合成が重い — このPC(省電力CPU+WSL2)では1文約6秒で電話には不適
                (2026-07-16実測)。強いマシンに載せ替えたら再検討
    """
    main = primary or os.environ.get("TTS_PROVIDER", "gemini")
    sub = fallback if fallback is not None else os.environ.get("TTS_FALLBACK", "google")
    voice = _make_one_tts(main)
    if not sub or sub == main or sub == "none":
        logger.info("TTS: %s (サブなし)", main)
        return voice
    try:
        backup = _make_one_tts(sub)
    except Exception:
        # サブの構成に失敗しても通話は止めない (メインが生きているなら喋れる)
        logger.exception("TTSのサブ(%s)を構成できなかった — メイン単体で続行", sub)
        return voice
    logger.info("TTS: %s (サブ: %s)", main, sub)
    return tts_mod.FallbackAdapter([voice, backup])


def _make_google_tts():
    """Google Cloud TTS (Chirp3-HD)。aivis のフォールバック先としても使う。"""
    # ストリーミング合成は初動0.11sと速いが、テキスト断片ごとの逐次合成で
    # 韻律が乱れる (2026-07-18実機)。文単位の一括合成 (初動0.3s) は品質が安定 —
    # 文分割・並列合成はフレームワークのStreamAdapterがやってくれる
    streaming = os.environ.get("GOOGLE_TTS_STREAMING", "0") == "1"
    kwargs = {}
    if not streaming:
        # ⚠一括合成APIはPCM(デフォルト)非対応 — LINEAR16必須
        # (PCMのままだと全合成が400エラーで無音になる。2026-07-18実機で被弾)
        kwargs["audio_encoding"] = gcloud_texttospeech.AudioEncoding.LINEAR16
    return google.TTS(
        language="ja-JP",
        voice_name=os.environ.get("GOOGLE_TTS_VOICE", "ja-JP-Chirp3-HD-Leda"),
        use_streaming=streaming,
        credentials_file=os.environ.get("GOOGLE_TTS_CREDENTIALS", "/secrets/callagent-tts.json"),
        **kwargs,
    )


def _make_session(tts_primary: str | None = None, tts_fallback: str | None = None) -> AgentSession:
    """AIコアは AI_CORE 環境変数で切替 (デフォルト: live)。

    - live:    Gemini Live API。音声→音声で速い・自然。声は GEMINI_VOICE
    - cascade: Deepgram STT → Gemini 3.5 Flash (thinking無効) → _make_tts()。
               賢さ優先だがターン遅延が大きい (2026-07-16実測で体感NG)
    """
    # ⚠割り込みの閾値を上げる (2026-07-30の実機テストで判明)。
    # 既定 (0.5秒・語数制限なし) だと、相手が電話に向けてではなく横で言った独り言
    # (「お礼言われた」) でAIが文の途中で黙り、相手からは「え、消えた」と見える。
    # 実際にそう言われた。相槌 (「うん」「はい」「ええ」) で止まるのも同じ理屈。
    # 0.8秒 + 2語以上を要求すると、相槌では止まらず、本当の割り込みには従う。
    # ⚠上げすぎると「もういいです」で切りたい相手を黙らせる無礼な機械になるので、
    #   自動テスト (相手役AIのシナリオ) で両方向を確認してから動かすこと
    interrupt_sec = float(os.environ.get("MIN_INTERRUPTION_SEC", "0.8"))
    interrupt_words = int(os.environ.get("MIN_INTERRUPTION_WORDS", "2"))
    if os.environ.get("AI_CORE", "live") == "cascade":
        return AgentSession(
            user_away_timeout=SILENCE_SEC,
            vad=silero.VAD.load(),
            stt=_make_stt(),
            min_interruption_duration=interrupt_sec,
            # 語数判定はSTTの結果を使うのでcascadeでのみ有効
            min_interruption_words=interrupt_words,
            # STT暫定結果でLLM+TTSを見切り発車し、確定と一致すればそのまま採用
            # (Google STTの確定待ち約1.2sを隠蔽。音質・応答内容への影響なし)
            preemptive_generation=True,
            # thinkingは遅延の主因なので最小化する。⚠Gemini 3系は thinking_budget=0 が
            # 「無視される」(警告ログのみ、こっそりthinkingが動く) — thinking_level が正
            # (2026-07-18 実機ログの警告で判明。LLM初トークン0.8〜1.2sの一因だった)
            # 🚨**3.6には戻さない** (2026-08-01夜に切り戻し)。同日昼に3.6へ上げたが、
            # **ツール宣言があるとき、関数呼び出しが本文に溢れる**のを実測した:
            #   「…失礼します。幕頭[call:default_api:end_call{}]」「…失礼します。 चिरend_call{}」
            #   「…失礼します。#CALL:default_api:end_call{}」「…失礼します。大吉」
            # ⚠**しかもその回はツールが呼ばれていない** = 切るつもりで切れていない。
            #   「end_call が実測4/5」と記録してきた現象の正体はこれの可能性が高い。
            # ⚠この文字列は**TTSに渡るので相手に聞こえている**。記録の汚れでは済まない。
            # 切り分け (infra/scripts/probe_stray_tokens.py、各15サンプル×3回):
            #   本番構成(3.6+ツール+本番プロンプト) 13/45 異常 / 3.5へ戻す **0/45** /
            #   ツール宣言を外す **0/45** / プロンプトを最小化 1/45 / 温度は無関係(0.2でも同率)
            #   → 3.6 × ツール宣言 の組み合わせが原因。プロンプトの長さは発生率を上げるだけ
            # 再挑戦するなら、上のスクリプトを回して 0/45 になってからにすること
            # ⚠テキストモデルも環境変数で差し替えられるようにした (2026-09-18)。
            #   API の models 一覧には 3.6 / 3.7 / 3.8-flash があるが、**既定は 3.5 のまま**。
            #   8/1 に 3.6 へ上げたとき、ツール宣言があると関数呼び出しが本文に溢れて
            #   相手に聞こえた (13/45)。上げるなら run_scenarios の no_tool_leak で測ってから。
            llm=google.LLM(
                model=os.environ.get("GEMINI_TEXT_MODEL", "gemini-3.5-flash"),
                temperature=0.7,
                thinking_config=genai_types.ThinkingConfig(thinking_level="low"),
            ),
            tts=_make_tts(tts_primary, tts_fallback),
        )
    return AgentSession(
        user_away_timeout=SILENCE_SEC,
        # ⚠**live でも手元の VAD が要る** (2026-09-18、ユーザーが実機で「自分で話していても
        #   『もしもし？』になる」と指摘して判明)。Gemini のプラグインは相手の発話開始を
        #   **割り込まれたときしか**フレームワークに伝えない (realtime_api.py の
        #   _handle_input_speech_started は interrupted と自発 generate の直前だけ)。
        #   だから user_state は一度も speaking にならず、AI が黙った SILENCE_SEC 秒後に
        #   相手が喋っていようが away → 「もしもし？聞こえていますか？」が必ず出ていた。
        #   turn ログ (06:24 の aizuchi) に user listening→speaking が 1 件も無いのが証拠。
        #   ターン検出そのものは引き続きサーバー側 (Gemini)。silero は user_state と
        #   割り込み判定 (min_interruption_duration) にだけ使われる
        vad=silero.VAD.load(),
        # s2sは語数判定に使えるSTT結果を持たないので秒数だけで抑える
        min_interruption_duration=interrupt_sec,
        # ⚠既定を **gemini-3.8-live** にした (2026-09-18)。7/30に「再検討する」と置いた
        #   2条件のうち②が解けたため。実測した capabilities:
        #     3.1-flash-live : mutable_instructions=False / mutable_chat_context=False
        #     3.8-live       : mutable_instructions=True  / mutable_chat_context=True
        #   ⚠**3.1では chat_ctx も更新できなかった**= inject_chat_text で届けている
        #     **耳打ちも後方支援メモも live 構成では一切届いていなかった**。
        #     (案件mdは「指示更新ができない」としか書いておらず、被害範囲を過小に記録していた)
        #   プラグイン自身も3.1に対して「will not be applied until the next session」と警告する。
        # ⚠モデルIDは実在を API の models 一覧で確認したもの。もう1つ
        #   `gemini-3.8-live-extended-thinking` があり capabilities は同じ。
        #   思考を入れると遅延が増えるはずなので、電話では既定にしない。
        llm=google.beta.realtime.RealtimeModel(
            model=os.environ.get("GEMINI_LIVE_MODEL", "gemini-3.8-live"),
            voice=os.environ.get("GEMINI_VOICE", "Leda"),
            language="ja-JP",
            temperature=0.8,
            # ⚠相手の「間」への食い付きを鈍らせる (2026-09-18)。
            #   3.8 は既定だと相手が「佐藤」で一拍置いた瞬間に返事を始め、相手の続きが
            #   拾われないまま無言扱いになった (実測 05:50:41、aizuchi の no_truncated_ai も同型)。
            #   電話は相槌と言い淀みが多いので、発話終了の判定を鈍く・沈黙の要求時間を長めにする。
            #   ⚠上げすぎると返事が遅れる (7/16「電話は遅延が先に体験を壊す」)。
            #     LIVE_END_SILENCE_MS で調整し、run_scenarios の aizuchi と ttft で両側を見る
            realtime_input_config=genai_types.RealtimeInputConfig(
                automatic_activity_detection=genai_types.AutomaticActivityDetection(
                    end_of_speech_sensitivity=genai_types.EndSensitivity.END_SENSITIVITY_LOW,
                    silence_duration_ms=int(os.environ.get("LIVE_END_SILENCE_MS", "800")),
                )
            ),
        ),
        # say() 用のTTS (Live APIと同系のLedaの声)。⚠**3.8でも supports_say=False のまま**なので、
        # 固定挨拶は引き続き say() で確定再生する (2026-07-17実機エラーで判明・2026-09-18に再確認)
        # ⚠管制室の声の設定 (tts_primary / tts_fallback) は **live では見ない** (2026-09-19)。
        #   ここで作る TTS は挨拶・無言時の促し・切る前の一言の 3 つにしか使われず、会話本体は
        #   Gemini の GEMINI_VOICE で喋る。設定で Aivis 等を選ぶと定型文だけ別人の声になるので、
        #   Live API と同系の Google Cloud TTS (GOOGLE_TTS_VOICE = Chirp3-HD の同名の声) に固定する。
        #   管制室の設定ページも live のときは声の選択を出さない (settings/page.tsx)
        tts=_make_tts("google", "gemini"),
    )


def _caller_number(ctx: JobContext) -> str | None:
    """SIP参加者の属性から発信者番号を拾う (Asterisk→LiveKit SIP経由)。
    テスト通話 (caller-sim) は属性が無いため、ルーム名 call_<番号>_xxx から拾う。"""
    for p in ctx.room.remote_participants.values():
        number = p.attributes.get("sip.phoneNumber")
        if number:
            return number
    m = re.match(r"call_(\+?\d+)_", ctx.room.name)
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


async def entrypoint(ctx: JobContext):
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    logger.info("joined room %s", ctx.room.name)

    # 通話行は入室した瞬間に作る (番号はルーム名 call_<番号>_xxx から即時取得)。
    # 管制室が発信者の参加より先に接続を始められる = 挨拶の頭切れ・画面表示の遅さ対策 (2026-07-18)
    caller = _caller_number(ctx)
    db = CallDb()
    await db.start_call(ctx.room.name, caller)

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
            try:
                await ctx.api.room.delete_room(lk_api.DeleteRoomRequest(room=ctx.room.name))
            except Exception:
                logger.exception("ブロックした呼のルーム削除に失敗")
            return

    writer = TranscriptWriter(ctx.room.name, caller)

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

    @session.on("conversation_item_added")
    def on_item(ev: ConversationItemAddedEvent):
        if isinstance(ev.item, ChatMessage) and ev.item.text_content:
            speaker = "caller" if ev.item.role == "user" else "ai"
            # 書記モードではAIの (音声にならない) 生成テキストを記録に残さない
            if scribe_mode and speaker == "ai":
                return
            interrupted = bool(getattr(ev.item, "interrupted", False))
            if speaker == "ai" and TOOL_LEAK.search(ev.item.text_content):
                # ⚠**消さずに残して、目立つように鳴らす** (2026-08-01)。
                #   関数呼び出しが本文に溢れる = その回はツールが呼ばれていない可能性が高い
                #   (切るつもりで切れていない)。文字列を消すと**実害の証拠だけが消えて
                #   症状が軽く見える**ので、記録はそのまま残し、ログと自動テストで捕まえる。
                #   詳しくは _make_session の llm= のコメント (3.6で実測した件)
                logger.error(
                    "🚨ツール呼び出しが本文に漏れた — モデルを疑うこと: %r",
                    ev.item.text_content[-60:],
                )
            writer.add_segment(speaker, ev.item.text_content, interrupted)
            asyncio.create_task(db.add_segment(speaker, ev.item.text_content, interrupted))

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

    async def on_shutdown():
        writer.close()
        await db.end_call()

    ctx.add_shutdown_callback(on_shutdown)

    # セッションが (エラーで) 閉じたらルームごと畳む — 相手を無音のまま待たせない・
    # 参加者退出→ジョブ終了→end_call の正規経路が確実に走る。プロセス即死の場合のみ
    # hookd の reaper が拾う (多層防御。2026-07-18: 孤児通話の根本対策)
    @session.on("close")
    def on_session_close(ev):
        err = getattr(ev, "error", None)
        if err is not None:
            logger.error("session closed with error: %s — ルームを畳んで通話を終了する", err)

        async def _teardown():
            try:
                await ctx.api.room.delete_room(lk_api.DeleteRoomRequest(room=ctx.room.name))
            except Exception:
                pass  # 既にルームが無い正常終了時はここに来る

        asyncio.create_task(_teardown())

    # M3 交代: 管制室から operator- が入室したら、AIは発話を止めて聞き役に降格する。
    # 文字起こし (入力音声の transcription) は継続する。handback (AIに戻す) は将来対応
    def _handover(identity: str):
        logger.info("operator joined: %s — AIを聞き役に降格", identity)
        try:
            session.interrupt()
        except Exception:
            pass
        try:
            session.output.set_audio_enabled(False)
        except Exception:
            logger.exception("agent音声出力の停止に失敗 (交代は継続)")
        if db.call_id is not None and db._pool:
            # 書記モード (受話ボタン経由) なら最初から人間が応答した通話
            by = "human" if scribe_mode else "ai_then_human"
            asyncio.create_task(
                db._pool.execute(
                    "UPDATE calls SET answered_by=$2 WHERE id=$1", db.call_id, by
                )
            )

    @ctx.room.on("participant_connected")
    def on_participant_connected(p: rtc.RemoteParticipant):
        if p.identity.startswith("operator-"):
            _handover(p.identity)

    # handback: operator (本人) が退室したら AI が応対に復帰する。
    # 管制室の「AIに任せる」ボタン = operator切断→モニタ再接続、なのでこれで拾える
    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(p: rtc.RemoteParticipant):
        if not p.identity.startswith("operator-"):
            return
        still = any(
            pp.identity.startswith("operator-") for pp in ctx.room.remote_participants.values()
        )
        if still or scribe_mode:
            return  # 別のoperatorが残っている / 書記モード (AIはそもそも喋らない)
        logger.info("operator left — AIが応対に復帰 (handback)")
        try:
            session.output.set_audio_enabled(True)
        except Exception:
            logger.exception("AI復帰に失敗")

    # 本人 (operator) の声は AgentSession の入力対象外 (sessionは発信者の音声だけを聴く) —
    # 専用のSTTストリームで文字起こしして speaker='user' として記録する (2026-07-18)。
    # これで交代後・書記モードの本人発話も画面/DB/吹き出し支援(watcher)に届く
    def _transcribe_operator(track: rtc.RemoteTrack, identity: str):
        async def run():
            # AudioStreamは通話中1本だけ持ち、STTストリームは落ちても張り直す。
            # マイク音声をpumpが「今のSTTストリーム」に流し込む (差し替え可能な参照経由)
            audio = rtc.AudioStream(track, sample_rate=16000, num_channels=1)
            current: dict = {"stream": None}
            stopped = asyncio.Event()

            async def pump():
                n = 0
                try:
                    async for ev in audio:
                        n += 1
                        if n == 1 or n % 500 == 0:
                            logger.info("operator音声 frame#%d (%s)", n, identity)
                        s = current["stream"]
                        if s is not None:
                            try:
                                s.push_frame(ev.frame)
                            except Exception:
                                pass  # 差し替えの隙間はフレームを捨てるだけ
                finally:
                    logger.info("operator音声 pump終了 (frames=%d, %s)", n, identity)
                    stopped.set()  # トラック終了 (operator退室/通話終了) = 本人発話の終わり

            pump_task = asyncio.create_task(pump())
            # STT張り直しループ: 本人が沈黙する時間帯があると Google Cloud STT が
            # Audio Timeout で確定クラッシュする (2026-07-19実機。「拾われてない」の真因は
            # 精度ではなく、一度落ちたSTTストリームが二度と復活しなかったこと)。
            # プロバイダは _make_stt() のまま (STT_PROVIDER設定を発信者用と共通で使う) —
            # 効くのは「落ちても即座に新しいストリームを張り直す」ことで、これはどの
            # プロバイダでも要る保険
            while not stopped.is_set():
                stream = _make_stt().stream()
                current["stream"] = stream
                try:
                    async for ev in stream:
                        if ev.type == lk_stt.SpeechEventType.FINAL_TRANSCRIPT and ev.alternatives:
                            text = ev.alternatives[0].text.strip()
                            if text:
                                writer.add_segment("user", text, False)
                                await db.add_segment("user", text, False)
                except Exception as e:
                    logger.info("operator STT張り直し (%s): %s", identity, type(e).__name__)
                finally:
                    current["stream"] = None
                    try:
                        await stream.aclose()
                    except Exception:
                        pass
                if not stopped.is_set():
                    await asyncio.sleep(0.3)  # 軽いバックオフ (張り直しの暴走防止)
            pump_task.cancel()
            logger.info("operator文字起こし終了 (%s)", identity)

        asyncio.create_task(run())

    @ctx.room.on("track_subscribed")
    def on_track_subscribed(
        track: rtc.RemoteTrack, pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant
    ):
        if p.identity.startswith("operator-") and track.kind == rtc.TrackKind.KIND_AUDIO:
            logger.info("operator音声を購読 — 本人の文字起こし開始 (%s)", p.identity)
            _transcribe_operator(track, p.identity)

    agent = PhoneAgent(db=db, caller=caller, job_ctx=ctx)
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

    def _on_user_state(ev) -> None:
        # ⚠喋り始めた瞬間に猶予を戻す (2026-09-18)。以前は user_input_transcribed (確定) だけで
        #   戻していたが、live の入力文字起こしは**AIが返事を生成し始めるまで届かない**ので、
        #   「促した → 相手が話し始めた → まだ文字が来ない → 10秒経過 → 切る」が起こりうる
        #   (ユーザーの指摘「無言判定が STT で文字が出るまでの時間になっている」がこれ)
        if getattr(ev, "new_state", "") == "speaking":
            silence_state["prompted"] = False
            silence_state["gen"] += 1
            return
        if scribe_mode or getattr(ev, "new_state", "") != "away":
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
        try:
            await ctx.api.room.delete_room(lk_api.DeleteRoomRequest(room=ctx.room.name))
        except Exception:
            logger.exception("無言による切断でroom削除に失敗 (通話は既に終わっている可能性)")

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
        logger.info(
            "turn: transcribed final=%s %r",
            getattr(ev, "is_final", None), (getattr(ev, "transcript", "") or "")[:40],
        )
        if getattr(ev, "is_final", False):
            silence_state["prompted"] = False
            silence_state["gen"] += 1  # 眠っている古い _handle_silence を無効化する

    # 指示文への動的注入: ①着信時の下調べ (caller_context) ②後方支援メモ (agent_note)。
    # ②は directory-agent worker が会話の文字起こしを監視し、役立つと判断した時だけ
    # 自発的に投入してくる — アシスタント側から調べに行くことはない (待たせる間が消える)
    extra = {"context": None}

    async def refresh_instructions():
        text = build_instructions(caller)
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
        if scribe_mode:
            logger.info("耳打ち即時応答なし (書記モード)")
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

    asyncio.create_task(inject_caller_context())
    asyncio.create_task(watch_agent_notes())

    if scribe_mode:
        session.output.set_audio_enabled(False)  # 挨拶もせず音声も出さない
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
    await speak_fixed(GREETING, allow_interruptions=False)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
