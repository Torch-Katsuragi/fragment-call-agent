# -*- coding: utf-8 -*-
"""Directory Agent worker — フラグメント専用ワークスペース(Google Drive)の番人。

役割 (ホスト側プロセスとして実行。コンテナではない — Drive for Desktop のG:を直接読み書きするため):
  1. agent_jobs キュー (postgres) を監視し、ask_workspace の質問に回答する
     - バックエンド auto: agy (Antigravity CLI) があれば使い、無ければ Gemini API 直呼び
  2. 終了した通話の文字起こしをDBから ワークスペース/通話記録/*.md に書き出す
     (ワークスペースを Drive などで同期すれば、他のツールや AI とも共有できる)
  3. 通話の録音を ワークスペース/録音/ へ移し、管制室の指定で消す (recordings.py)

起動: python services/directory-agent/worker.py  (リポジトリどこからでも可)
環境変数: DATABASE_URL / VERTEX_PROJECT か GOOGLE_API_KEY (無ければリポジトリ直下の .env から読む)。
  Gemini の呼び先は vertex.py (VERTEX_PROJECT があれば Vertex AI、2026-10-08)
          FRAGMENT_WORKSPACE (デフォルト: ./workspace)
          DIRECTORY_AGENT_BACKEND = auto | agy | gemini
"""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

import phonebook as pb
import recordings
import vertex

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("directory-agent")

# ホスト実行時はリポジトリルート (2階層上)。コンテナ実行時は /app/worker.py で階層が
# 足りないため自ディレクトリで代替する (env はcomposeから来るので .env フォールバックは不要)
try:
    REPO_ROOT = Path(__file__).resolve().parents[2]
except IndexError:
    REPO_ROOT = Path(__file__).resolve().parent
WORKSPACE = Path(
    os.environ.get("FRAGMENT_WORKSPACE", r"./workspace")
)
BACKEND = os.environ.get("DIRECTORY_AGENT_BACKEND", "auto")
# ⚠Google の最新 Flash に追従する方針 (2026-09-25 ユーザー「言わなくてもまめにアプデ」)。
#   3.6→3.8: 番号検索の実測で 3.6 3〜5秒 / 3.8 2〜6秒、地元の番号 (地元の事業所・役場) は 3.8 が当てる。
#   Flash-Lite は1.5秒だが地元の番号を全部「不明」にしたので速さのためには下げない
GEMINI_MODEL = os.environ.get("DIRECTORY_AGENT_MODEL", "gemini-3.8-flash")
# 取り次ぎ要求の宛先 (判断層のOR発火)。ホスト実行時はhookdの公開ポート、
# コンテナ実行時はcomposeが HOOKD_URL=http://hookd:8790 を注入する。
# ⚠host.docker.internal を既定にしない — Linuxでは解決できず、agent側で
#   「DNSエラーのフォールバックが正常系と同じ台詞を言う」偽動作を起こした (2026-08-01)
HOOKD_URL = os.environ.get("HOOKD_URL", "http://127.0.0.1:8790")


def _load_env_file() -> dict[str, str]:
    env: dict[str, str] = {}
    p = REPO_ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


_ENVF = _load_env_file()
DSN = os.environ.get("DATABASE_URL") or _ENVF.get(
    "DATABASE_URL", "postgresql://callagent:callagent@localhost:5432/callagent"
)
# Gemini の呼び先 (vertex.py が環境変数を見る)。手元で動かすときは .env の値を使う
for _k in ("GOOGLE_API_KEY", "VERTEX_PROJECT"):
    if not os.environ.get(_k) and _ENVF.get(_k):
        os.environ[_k] = _ENVF[_k]

# 本人 (電話の持ち主) の呼び名。AI への指示文の {OWNER} に入る。
# ⚠未設定なら「持ち主」。氏名をソースに書かない (公開リポジトリに載るため)
OWNER_NAME = os.environ.get("OWNER_NAME", "").strip() or "持ち主"

SYSTEM_PROMPT = """あなたは{OWNER}の電話応対AI「フラグメント」の後方支援エージェントです。
作業場はフラグメント専用ワークスペース (共有メモ/連絡先/通話記録/受信箱) です。
電話AIからの質問に、電話口でそのまま読み上げられる簡潔な日本語 (1〜3文、箇条書き・記号なし) で答えてください。
- ワークスペース内に情報がなければ「該当する情報は見つかりませんでした」と答える
- 【本人限定】と書かれた情報は回答に含めない
- パスワード・口座番号などの機微情報は、もし見つけても絶対に回答に含めない"""


def gather_workspace(max_chars: int = 40000) -> str:
    """ワークスペースのmdを集めて1つのコンテキスト文字列に (通話記録は新しい順に一部)。"""
    parts: list[str] = []
    dirs = ["共有メモ", "連絡先", "受信箱"]
    files: list[Path] = []
    for d in dirs:
        if d == "連絡先":
            # 2026-09-26 から 連絡先/<番号>/<番号>.md と 連絡先/<番号>/<人>.md。⚠_未使用/ は読まない
            files.extend(
                f for f in sorted((WORKSPACE / d).rglob("*.md"))
                if "_未使用" not in f.parts
            )
            continue
        files.extend(sorted((WORKSPACE / d).glob("*.md")))
    records = sorted((WORKSPACE / "通話記録").glob("*.md"), reverse=True)[:10]
    files.extend(records)
    total = 0
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        chunk = f"===== {f.relative_to(WORKSPACE)} =====\n{text}\n"
        if total + len(chunk) > max_chars:
            break
        parts.append(chunk)
        total += len(chunk)
    return "\n".join(parts)


def call_gemini(
    system_prompt: str,
    user_text: str,
    json_mode: bool = False,
    google_search: bool = False,
    cached: str | None = None,
) -> str:
    # ⚠thinkingBudget:0 は使わない (2026-08-01)。3.5では黙って無視されていたが、
    #   **3.6は400で拒否する** — このワンパラメータでworkerのGemini呼び出しが全滅し、
    #   下調べ・番号web検索・吹き出し・後方支援メモ・取り次ぎ判断が全部沈黙した。
    #   agent側は7/18に thinking_level へ直していて (同じ罠のSDK側)、こちらはREST残存だった。
    #   thinkingLevel:"low"+json_mode / +google_search の併用可はREST実測済み (200)
    gen_cfg: dict = {"temperature": 0.3, "thinkingConfig": {"thinkingLevel": "low"}}
    if json_mode:
        gen_cfg["responseMimeType"] = "application/json"
    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": gen_cfg,
    }
    if cached:
        # 明示キャッシュ (指示文と前半は登録済み)。⚠system_instruction は併記できない
        del body["system_instruction"]
        body["cachedContent"] = cached
    if google_search:
        # Google検索グラウンディング (⚠json_modeとは併用不可)
        body["tools"] = [{"google_search": {}}]
    data = _gemini_rest("POST", f"models/{GEMINI_MODEL}:generateContent", body, timeout=60)
    # 使った量を残す (2026-10-02、費用の内訳を測るため)。cached = 暗黙キャッシュに当たった分 (安い)
    u = data.get("usageMetadata") or {}
    log.info(
        "gemini usage [%s]: prompt=%s cached=%s out=%s",
        _PROMPT_TAGS.get(system_prompt, system_prompt[:12].replace("\n", " ")) + ("+cache" if cached else ""),
        u.get("promptTokenCount"), u.get("cachedContentTokenCount", 0),
        (u.get("candidatesTokenCount") or 0) + (u.get("thoughtsTokenCount") or 0),
    )
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


# usage のログで呼び出しの種類を見分ける名前 (定義後に埋める)
_PROMPT_TAGS: dict[str, str] = {}


def answer_with_gemini(question: str) -> str:
    ctx = gather_workspace()
    return call_gemini(
        SYSTEM_PROMPT, f"# ワークスペースの内容\n{ctx}\n\n# 電話AIからの質問\n{question}"
    )


def answer_with_agy(question: str) -> str:
    """Antigravity CLI (agy) をワークスペース内でヘッドレス実行。
    フラグ体系はインストール後に `agy --help` で確定させること (AGY_ARGS で上書き可)。"""
    args_tmpl = os.environ.get("AGY_ARGS", "--headless -p {prompt}")
    prompt = f"{SYSTEM_PROMPT}\n\n質問: {question}"
    args = ["agy"] + [a.replace("{prompt}", prompt) for a in args_tmpl.split(" ")]
    proc = subprocess.run(
        args, cwd=str(WORKSPACE), capture_output=True, text=True, encoding="utf-8", timeout=120
    )
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        raise RuntimeError(f"agy failed rc={proc.returncode}: {(proc.stderr or '')[:300]}")
    return out


LOOKUP_PROMPT = """あなたは電話番号の身元調査係です。Google検索を使って、与えられた
電話番号の情報 (所有者・事業者名・迷惑電話/営業/詐欺の報告) を調べます。
出力形式 (厳守、3行):
name: 事業者名・施設名 (着信画面に出す。20字以内。特定できなければ 不明)
verdict: normal / sales / scam / unknown のいずれか
  - sales / scam は検索結果に明確な報告がある場合のみ。**迷ったら unknown** (誤判定は
    正当な着信の自動拒否につながる)
summary: 分かったことの要約 (1〜2文。何も見つからなければ「情報なし」)"""


def lookup_number(number: str) -> str:
    """番号をweb検索して {"name", "verdict", "summary"} のJSON文字列を返す。
    name は着信画面の「番号検索中…」を置き換える (特定できなければ null)。"""
    out = call_gemini(LOOKUP_PROMPT, f"電話番号: {number}", google_search=True)
    m = re.search(r"verdict:\s*(normal|sales|scam|unknown)", out)
    verdict = m.group(1) if m else "unknown"
    nm = re.search(r"^\s*name:\s*(.+?)\s*$", out, flags=re.M)
    name = nm.group(1).strip("「」\"' ") if nm else ""
    if name in ("", "不明", "情報なし", "なし"):
        name = None
    sm = re.search(r"summary:\s*(.+)", out, flags=re.S)
    summary = (sm.group(1) if sm else re.sub(r"^.*(name|verdict):.*$", "", out, flags=re.M)).strip()
    return json.dumps(
        {"name": name[:40] if name else None, "verdict": verdict, "summary": summary[:300] or "情報なし"},
        ensure_ascii=False,
    )


DIGEST_PROMPT = """電話の管制室に浮かべる「相手の下調べ」カードを作ります。
素材 (電話帳メモ・過去の通話・番号のweb検索結果) を読み、本人 ({OWNER}) が通話中に
一瞥して役立つ考察へ凝縮してください。読者は{OWNER}本人です:
- **本人へのブリーフィング文体**で書く。命令形・依頼形 (「〜してください」) は使わない
- 【本人限定】情報もそのまま書いてよいが、「本人限定のため〜」のような制限の説明はしない
出力JSON: {"title": "📇 相手の名前や正体 (12字程度)", "text": "考察 2〜4文。関係・直近のやりとり・注意点"}"""


def digest_intro(raw: str, lookup: str | None, number: str) -> tuple[str, str]:
    user = (
        f"# 番号\n{number}\n\n# 電話帳メモと直近の通話\n{raw}\n\n"
        f"# 番号のweb検索結果\n{lookup or '(なし)'}"
    )
    data = json.loads(call_gemini(DIGEST_PROMPT, user, json_mode=True))
    title = str(data.get("title") or "📇 相手の下調べ").strip()[:40]
    text = str(data.get("text") or "").strip() or raw[:400]
    return title, text


def resolve_backend() -> str:
    if BACKEND in ("agy", "gemini"):
        return BACKEND
    return "agy" if shutil.which("agy") else "gemini"


BOOK = pb.Book(WORKSPACE / "連絡先")


KANA_PROMPT = """日本人の名前 (または店・会社の名前) の読みがなを、ひらがなで返します。
姓と名の間は半角スペース。読みが複数ありうる字は、いちばんよくある読みにします。
名前でないもの (「〇〇の携帯」「テスト用」などの説明) は、説明の部分も含めて全体を読みます。
出力JSON: {"kana": "..."}"""


def fill_phonebook_kana() -> None:
    """読みがなの無い電話帳md に kana を補う (1 回 1 件、2026-09-26)。アプリの電話帳の並び順に使う。
    ⚠スマホの連絡先から登録したものは連絡先のふりがなが入っているので、ここに来るのはそれ以外
    (手で作ったもの・ふりがなの無い連絡先)。推測なので、違っていたら md の kana を直せばよい"""
    for number in BOOK.numbers():
        p = BOOK.file(number)
        try:
            md = p.read_text(encoding="utf-8")
        except Exception:
            continue
        name, _ = pb.name_state(md)
        if not name or name == "不明" or re.search(r"^kana:", md, re.M):
            continue
        try:
            kana = str(json.loads(call_gemini(KANA_PROMPT, name, json_mode=True)).get("kana") or "").strip()
        except Exception:
            log.exception("kana failed: %s", number)
            return
        if not kana:
            return
        kana = re.sub(r"[\r\n\"]", " ", kana)[:60]
        p = BOOK.ensure(number)  # 旧形式ならここで新しい形へ移る
        md = p.read_text(encoding="utf-8")
        p.write_text(re.sub(r"^(name:.*)$", lambda mm: f"{mm.group(1)}\nkana: {kana}", md, count=1, flags=re.M), encoding="utf-8")
        log.info("kana %s: %s → %s", number, name, kana)
        return


def update_phonebook_after_call(c, summary: str, org: str, person: str, conflict: str = "") -> None:
    """終話した通話で、相手の電話帳を更新する (2026-09-26。書き手の決まりは phonebook.py の冒頭)。
    - 名前 (name) は**番号の持ち主**。組織の番号なら組織名で、そこからかけてくる個人は入れない
      (ユーザー「組織の番号は何人も使うので、名前欄に『組織の誰々』と書くのは不適切」)。
      「不明」のときだけ入れる。通話で名乗った組織 > 番号検索の名前 > (組織の手がかりが無ければ)
      名乗った個人名。どこから取ったかを name_source に残す。人が書いた名前 (name_source が無い) は
      触らない。番号検索の名前は、後の通話で組織を名乗られたらそちらに置き換える
    - 話した人: 名乗った個人を 連絡先/<番号>/<人>.md に日付と用件つきで残す (個人の番号なら作らない)。
      同じ人か迷ったら作らずに「未整理（自動）」へ
    - 最近の用件（自動）: 要約を日付つきで 1 行足す (新しい順に 10 行まで)
    - 番号検索（自動）: 検索結果の節を最新にする
    - 食い違い（自動）: 番号検索や登録済みの名前と違う組織を名乗られたら ⚠ で残す (通話が正)
    ⚠テスト通話 (ルーム名が sim) と、遡って要約した古い通話 (終話から 1 日より前) は触らない"""
    number = c["caller_number"] or ""
    if not pb.is_number(number) or c["room_name"].rsplit("_", 1)[-1].startswith("sim"):
        return
    if (datetime.now(timezone.utc) - c["ended_at"]).total_seconds() > 86400:
        return
    path = BOOK.ensure(number)
    md = path.read_text(encoding="utf-8")
    before = md
    name, source = pb.name_state(md)
    looked = latest_lookup_name(number)
    t = c["started_at"].astimezone()
    day = f"{t.month}/{t.day}"
    # 登録済みの名前 (人が書いたもの・前の通話で名乗られたもの) と違う組織を名乗られたら、名前は変えずに
    # 「食い違い（自動）」へ残す (2026-09-26 ユーザー「組織名の食い違いも ⚠ で残して」)。
    # ⚠番号検索で入れた名前との食い違いは、下の lookup_conflict が扱う (名前は名乗りの方に直す)
    if org and name not in ("", "不明") and source != "番号検索" and not pb.same_org(org, name):
        md = pb.note_conflict(md, f"- ⚠ {day} の通話では「{org}」と名乗った (登録は「{name}」)")
    if org and (name in ("", "不明") or source == "番号検索"):
        md = pb.set_name(md, org, "通話で名乗った")
    elif name in ("", "不明") and looked:
        md = pb.set_name(md, looked, "番号検索")
    elif name in ("", "不明") and person:
        # 組織の手がかりが何も無い = 個人の電話と見なす
        md = pb.set_name(md, person, "通話で名乗った")
    line = f"- {day} {summary}" if summary else f"- {day} 通話"
    owner, _ = pb.name_state(md)
    if person and pb.norm_person(person) != pb.norm_person(owner):
        # 人のファイルを先に書き、番号のファイルは書き直した後で読み直す (未整理に積まれることがある)
        if md != before:
            path.write_text(md, encoding="utf-8")
            before = md
        kind = BOOK.add_person_line(number, person, line)
        log.info("phonebook person %s/%s: %s", number, person, kind)
        md = path.read_text(encoding="utf-8")
        before = md
    md = pb.set_section(md, "話した人", BOOK.people_index(number))
    md = set_lookup_section(md, number)
    if conflict:
        # 通話の内容を正とする。番号検索と違うことが言われたら、その旨を残す
        md = pb.note_conflict(md, f"- ⚠ {day} 番号検索と食い違い: {conflict}")
    if summary:
        md = pb.add_to_section(md, "最近の用件", line, limit=10)
    if md != before:
        path.write_text(md, encoding="utf-8")
        log.info("phonebook updated after call: %s", number)


def latest_lookup_name(number: str) -> str | None:
    """番号検索で分かった名前のうち新しいもの。⚠同期関数 (ファイル書き込みと同じスレッドで使う)"""
    lk = _LOOKUP.get(number)
    return lk["name"] if lk else None


# 番号 → 名前が分かった番号検索の結果のうち新しいもの {name, summary, verdict, at}。30 秒ごとに作り直す。
# ⚠名前の分からなかった回 (「情報なし」) は数えない — 同じ番号でも検索のたびに結果が揺れる
#   (同じ番号で組織名と「該当なし」の両方が出た)
_LOOKUP: dict[str, dict] = {}


async def refresh_lookup_names(pool: asyncpg.Pool) -> None:
    rows = await pool.fetch(
        """SELECT DISTINCT ON (query) query, result, finished_at FROM agent_jobs
           WHERE kind = 'number_lookup' AND status = 'done' AND result LIKE '%"name": "%'
           ORDER BY query, id DESC"""
    )
    out = {}
    for r in rows:
        try:
            j = json.loads(r["result"])
        except Exception:
            continue
        if j.get("name"):
            out[r["query"]] = {
                "name": str(j["name"])[:40],
                "summary": str(j.get("summary") or "")[:300],
                "verdict": j.get("verdict") or "unknown",
                "at": r["finished_at"],
            }
    _LOOKUP.clear()
    _LOOKUP.update(out)


def _lookup_text(number: str) -> str:
    """プロンプトに渡す番号検索の結果 (無ければ空)"""
    lk = _LOOKUP.get(number)
    return f"{lk['name']} — {lk['summary']}" if lk else ""


VERDICT_LABEL = {"normal": "通常", "sales": "営業", "scam": "詐欺の報告あり", "unknown": "不明"}


def set_lookup_section(md: str, number: str) -> str:
    """「## 番号検索（自動）」を最新の検索結果で書き直す (2026-09-26、ユーザー「検索結果をもとに
    情報を充実させていい。そのうえで通話の内容を正として書き換えていく」)。
    ⚠節の中の「⚠」で始まる行 (通話との食い違いの記録) は残す"""
    lk = _LOOKUP.get(number)
    if not lk:
        return md
    at = lk["at"].astimezone() if lk.get("at") else None
    when = f" ({at.month}/{at.day} 調べ)" if at else ""
    lines = [f"- {lk['name']}{when} · {VERDICT_LABEL.get(lk['verdict'], lk['verdict'])}"]
    if lk["summary"]:
        lines.append(f"- {lk['summary']}")
    keep = [l for l in pb.section_items(md, "番号検索") if l.startswith("- ⚠")]
    return pb.set_section(md, "番号検索", "\n".join(lines + keep))


def fill_phonebook_names() -> None:
    """番号検索で分かったことを電話帳に入れる (1 回 1 件)。名前が「不明」なら名前も。
    通話の後の更新 (update_phonebook_after_call) より前からある番号の追いつき用"""
    for number in BOOK.numbers():
        try:
            md = BOOK.file(number).read_text(encoding="utf-8")
        except Exception:
            continue
        name, _ = pb.name_state(md)
        looked = latest_lookup_name(number)
        if not looked:
            continue
        next_md = md
        if name in ("", "不明"):
            next_md = pb.set_name(next_md, looked, "番号検索")
        if not pb.section_items(next_md, "番号検索"):
            next_md = set_lookup_section(next_md, number)
        if next_md != md:
            BOOK.ensure(number).write_text(next_md, encoding="utf-8")
            log.info("phonebook enriched from lookup: %s (%s)", number, looked)
            return


def phonebook_path(number: str) -> Path | None:
    """番号のファイル (読む用。無ければ None)"""
    return BOOK.file(number)


async def build_caller_context(pool: asyncpg.Pool, number: str) -> str:
    """着信時の下調べ: 電話帳 (番号のファイル + 話した人のファイル) + 直近の通話の抜粋。LLMは通さず即答する。"""
    parts: list[str] = []
    p = phonebook_path(number)
    if p:
        parts.append(f"【電話帳メモ ({p.name})】\n{p.read_text(encoding='utf-8')[:4000]}")
        for person in BOOK.people(number)[:8]:
            parts.append(f"【この番号から話した人 ({person.stem})】\n{person.read_text(encoding='utf-8')[:1200]}")
    else:
        parts.append("【電話帳メモ】この番号のメモはまだ無い (記録上は初対面の可能性が高い)")
    rows = await pool.fetch(
        """SELECT c.started_at, ts.speaker, ts.text
           FROM calls c JOIN transcript_segments ts ON ts.call_id = c.id
           WHERE c.caller_number = $1 AND c.ended_at IS NOT NULL
           ORDER BY c.started_at DESC, ts.seq DESC LIMIT 12""",
        number,
    )
    if rows:
        lines = [
            f"[{r['started_at'].astimezone():%m-%d %H:%M}] "
            f"{SPEAKER_LABEL.get(r['speaker'], r['speaker'])}: {r['text']}"
            for r in reversed(rows)
        ]
        parts.append("【この番号との直近の通話の抜粋 (古い→新しい)】\n" + "\n".join(lines))
    else:
        parts.append("【直近の通話】この番号との通話記録なし")
    return "\n\n".join(parts)


async def process_jobs(pool: asyncpg.Pool) -> None:
    # whisper (本人→AIの耳打ち) は agent が直接回収するので worker は触らない。
    # number_lookup は lookup_loop が別に回す
    job = await pool.fetchrow(
        """UPDATE agent_jobs SET status = 'running'
           WHERE id = (SELECT id FROM agent_jobs
                       WHERE status = 'pending' AND kind NOT IN ('whisper', 'number_lookup')
                       ORDER BY id LIMIT 1)
           RETURNING id, kind, query"""
    )
    if not job:
        return
    backend = resolve_backend()
    log.info("job %s (%s) via %s: %s", job["id"], job["kind"], backend, job["query"][:80])
    try:
        loop = asyncio.get_running_loop()
        if job["kind"] == "caller_context":
            number = job["query"].split(":", 1)[-1]
            result = await build_caller_context(pool, number)
        elif job["kind"] == "recording_delete":
            # 管制室の「期間を指定して消す」(2026-10-02)。Gemini は使わない
            result = await recordings.run_delete_job(pool, WORKSPACE, job["query"])
        else:
            fn = answer_with_agy if backend == "agy" else answer_with_gemini
            result = await loop.run_in_executor(None, fn, job["query"])
        await pool.execute(
            "UPDATE agent_jobs SET status='done', result=$2, finished_at=now() WHERE id=$1",
            job["id"],
            result,
        )
        log.info("job %s done: %s", job["id"], result[:100])
    except Exception as e:
        log.exception("job %s failed", job["id"])
        await pool.execute(
            "UPDATE agent_jobs SET status='error', result=$2, finished_at=now() WHERE id=$1",
            job["id"],
            str(e)[:500],
        )


SPEAKER_LABEL = {
    "caller": "相手",
    "ai": "AI",
    "user": "本人",
    "whisper": "本人の耳打ち(相手には聞こえていない)",
}

# ---- 会話監視 (後方支援メモ) -------------------------------------------------
# 進行中の通話の文字起こしを監視し、ワークスペースの情報が役立つと判断した時だけ
# agent_note ジョブとして短いメモを投入する。電話AI側はこれを指示文に取り込む。
# トリガーは常にこちら側 — 電話AIは調べに行かない (「お調べします」の間を無くすため)

WATCHER_PROMPT = """あなたは{OWNER}の電話応対AI「フラグメント」の後方支援エージェントです。
進行中の通話の文字起こしとワークスペース (共有メモ/連絡先/通話記録/受信箱) を見て、
次のJSONだけを出力します:

{"ai_note": "電話AIへの応対メモ (不要なら null)",
 "fragments": [{"kind": "info", "title": "短い見出し", "text": "クリックで展開される内容"}],
 "handoff": false,
 "handoff_reason": "本人を呼ぶ理由 (handoffがtrueのときだけ)"}

# ai_note — 電話AI (音声アシスタント) に渡す応対メモ。相手に聞こえる前提で書く
- ワークスペースに応対の役に立つ情報がある時だけ、そのまま応対に使える1〜2文
- **【本人限定】情報とパスワード等の機微情報は絶対に含めない** (相手に漏れるリスク)。
  必要なら「〜の件は『こちらでは答えられない』と答えて」の形にする
- 役立つ新情報がない・既送メモと重複する場合は null

## ⚠音声アシスタント側の禁止事項を破らせないこと (2026-07-31)
このメモは**アシスタントの文脈にそのまま入る**ので、ここに書いたことがそのまま実行される。
アシスタントには次を禁じてあるので、**メモでそれをやらせてはいけない**。
実際に「折り返し先の番号『090-xxxx-xxxx』で合っているか、復唱してご確認ください」という
メモを出して、番号の読み上げ禁止を裏から破っていた。

- ⚠**電話番号を書かない・復唱させない**。番号はSIPから取れていてDBに正確に残るので、
  口頭確認に意味がない (通話が間延びするだけ)
- ⚠**持ち主の氏名を書かない**。アシスタントには氏名を渡していない — 書くと渡ってしまう
- ⚠**持ち主の在否・状況を書かない** (「会議中」「戻りは◯時」等)。判断材料であって発話材料ではない
- ⚠**約束をさせない**。「折り返すと伝えて」「今日中にと答えて」は禁止。
  アシスタントの仕事は伝言に終始することで、折り返すかどうかは記録を見た持ち主が決める
- ⚠**相手の氏名を復唱させない**。「◯◯様ですね、と確認して」も不要 (記録に残る)
- ⚠**間違い電話と決めつけない** (2026-09-30)。この電話は個人の携帯とは限らない (固定電話・組織の番号にも使う)。
  「この電話について」の「名前を出してよい人」に載っている人宛てなら、正しい宛先。載っていない名前でも、
  相手自身が別の宛先だと言わない限り、用件を伺う方へ進めさせる。「個人の電話」と言わせない

# handoff — 本人の携帯を鳴らすか (取り次ぎの判断。判断層はあなたの担当)
⚠**これは持ち主が何をしていても中断させる重い操作。既定は false。**
次のどれかに当てはまるときだけ true にする:
- 事故・トラブル・体調など、**性質上あとに回せない**用件
- **持ち主が待っている相手**だと共有メモに書いてある
- 相手が急いでいる理由が具体的で、伝言では間に合わないと分かる

⚠**相手が呼び出しを許可されているかは仕組みの側が判断する。** あなたは「用件が緊急か」だけを
見て true/false を出せばよい。許可されていない相手なら発火は実行されない。

false にするもの (伝言で受ければ足りる):
- 「直接話したい」「代わってほしい」と言っているだけで、**用件が急ぎだと分からない**
- 初対面・ワークスペースに情報がない相手
- 営業・勧誘・問い合わせ
- 用件を伝えれば済むもの

⚠**空振りは安くない。** 鳴らしても大した用ではない、を繰り返すと持ち主は呼び出しを
無視するようになり、**本当に緊急のときに届かなくなる**。呼び出しの価値は希少性で保たれる。
持ち主が管制室を見ているなら自分で通話に入れるので、この呼び出しは
**「見ていない持ち主を引っ張り出す」ための最後の手段**だと考えること。
- 電話AI自身も取り次ぎツールを持っている。**あなたはその保険** — AIが呼び損ねたときにも
  届くように独立に判断するが、**基準は同じ**。AIが呼ばなかったからといって緩めない

# handoff_reason — 呼び出しの理由 (handoffがtrueのときだけ)
⚠**これは本人のスマホの着信画面に、相手の名前の下にそのまま表示される。**
出るか出ないかを数秒で決めるための一文なので、**誰が・何の件で呼んでいるか**を具体的に書く。
- 25文字程度。体言止めでよい
- (良い例)「山田様より、見積もりの件で直接お話ししたいとのご希望」
- (悪い例)「判断層: 相手が直接の対話を望んでいる」← 内部の用語で、判断の材料にならない
- ⚠**【本人限定】情報はここに書かない**。着信画面は**ロック画面の上に出る**ので、
  机に置いた端末を他人が覗ける (経緯や吹き出しはロック解除後にだけ表示される)

# fragments — 管制室に浮かべる吹き出し (思考の断片)。読者は持ち主本人
- **本人への情報支援であって、誰かへの指示ではない**。命令形・依頼形 (「〜してください」
  「〜と応対して」) は使わない。事実・経緯・気づきを簡潔に伝えるブリーフィング文体で書く
  (× 「折り返しを約束してください」 → ○ 「7/25夜の食事の誘いに折り返し約束済み・未返答」)
- 提案があるときは観察の形で (例: 「込み入った依頼なので交代した方が良さそう」)
- 【本人限定】情報もそのまま書いてよい。**「本人限定のため〜」のような制限への言及はしない**
  (本人の画面なので説明不要。内容だけ書く)
- 各吹き出しは title と text で構成される:
  - title: 画面に常時見える**短い見出し** (15文字程度まで。例:「前回の食事の誘い」「カマかけ注意」)
  - text: クリックで展開される内容 (1〜3文。詳細・根拠・気づきはこちらへ)
- **fragmentsは「いま画面に表示すべき吹き出しの完全なリスト」**。あなたが吹き出し群を丸ごと管理する:
  - 表示を続けたい吹き出し → 「現在表示中の吹き出し」から**一字一句同じ kind/title/text で再掲**する
    (少しでも変えると別の吹き出しとして作り直され、画面がちらつく)
  - もう不要になった吹き出し → リストに含めない (画面から消える)
  - 新しい気づき → 追加する。まとめ直し・要約への置き換えも自由
- kind は "info"(関連情報) / "alert"(注意・警戒) / "hint"(提案) を基本に自由
- 画面が煩雑にならないよう、常時2〜6枚程度に整理する
- **番号検索と食い違ったら明記する** (2026-09-26): 「番号のweb検索結果」が付いていて、相手が名乗った
  会社・団体・立場や話の内容がそれと食い違うときは、kind "alert"・title「番号検索と食い違い」の
  吹き出しで「番号検索では〇〇。通話では△△と名乗っている」と両方を書く。通話の内容を正として扱う
  (番号の使い回し・転送・なりすましのどれもありうるので、どちらが正しいかの断定はしない)。
  個人名が加わっただけ (テナント → テナントの山田) は食い違いではない"""

def parse_json_object(raw: str) -> dict:
    """LLMの出力から最初のJSONオブジェクトを取り出す。

    ⚠json.loads そのままだと落ちる (2026-07-31に実測)。実際のログ:
        json.decoder.JSONDecodeError: Extra data: line 3 column 1 (char 225)
      JSONの後ろに説明文や2つ目のJSONが付いてくることがあり、そのたびに
      「watch output failed (継続)」で**そのラウンドの応対メモと吹き出しが丸ごと捨てられていた**。
      continue するので通話は続き、気づきにくい形で支援だけが欠ける。
    先頭の { から括弧の対応が取れた位置までを切り出して読む (文字列内の括弧は数えない)。
    """
    s = raw.strip()
    if s.startswith("```"):  # ```json ... ``` で包まれる場合
        s = re.sub(r"^```[a-zA-Z]*\n?|```$", "", s).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    start = s.find("{")
    if start < 0:
        raise ValueError("JSONオブジェクトが見つからない")
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(s[start:], start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(s[start:i + 1])
    raise ValueError("JSONオブジェクトが閉じていない")


_watch_seen: dict[str, int] = {}  # call_id -> 最後に処理した相手発話のseq


async def line_identity(pool: asyncpg.Pool) -> str:
    """この電話の名乗りと、名前を出してよい人 (2026-09-30、管制室の設定)。後方支援に渡す。
    ⚠渡さないと、名乗りが個人の苗字のとき「個人の電話だから山田課長はいない、間違い電話として切れ」と
      メモを出し、応対中の AI に伝言の途中で番号違いを言わせた (staff_natural シナリオで実測)"""
    try:
        rows = await pool.fetch("SELECT key, value FROM settings WHERE key IN ('self_label', 'staff')")
    except Exception:
        return ""
    got = {r["key"]: r["value"] or "" for r in rows}
    try:
        staff = [s for s in json.loads(got.get("staff") or "[]") if isinstance(s, dict) and s.get("surname")]
    except json.JSONDecodeError:
        staff = []
    names = "、".join(s["surname"] + (f" ({s['title']})" if s.get("title") else "") for s in staff)
    return f"名乗り: {got.get('self_label') or '(なし)'}\n名前を出してよい人: {names or '(なし)'}"


# 会話監視の明示キャッシュ (2026-10-02)。通話ごとに 指示文 + 管制室の設定 + ワークスペース を登録し、
# 相手が話すたびの呼び出しでは残り (メモ・吹き出し・文字起こし) だけを送る。
# なぜ: 監視は相手の発話ごとに約 9,000 トークンを丸ごと送っていて、暗黙キャッシュは 3.8-flash では
#   一度も当たらなかった (同じ前半を 2 秒おきに 4 回送って cached=0)。明示キャッシュなら
#   ほぼ全部が cached になる (13,020/13,031)。中身は同じ文章を 2 つに分けて渡すだけ
# ⚠作れなかったら (短すぎる・API の失敗) 従来どおり全部送る。費用が戻るだけで動きは変わらない
_watch_cache: dict[str, tuple[int, str]] = {}  # call id → (前半の hash, "cachedContents/…" か "")
WATCH_CACHE_TTL = "1800s"  # 通話より長く。終話を見たら消す (消し損ねても TTL で消える)


def _gemini_rest(method: str, path: str, body: dict | None = None, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        vertex.url(path),
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method,
        headers=vertex.headers(),
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        raw = res.read()
    return json.loads(raw) if raw else {}


def _watch_cache_for(call_key: str, prefix: str) -> str | None:
    h = hash(prefix)
    got = _watch_cache.get(call_key)
    if got and got[0] == h:
        return got[1] or None
    if got:  # ワークスペースが変わった (電話帳の追記など) — 作り直す
        _drop_watch_cache(call_key)
    try:
        c = _gemini_rest("POST", "cachedContents", {
            "model": vertex.model_name(GEMINI_MODEL),
            "systemInstruction": {"parts": [{"text": WATCHER_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": prefix}]}],
            "ttl": WATCH_CACHE_TTL,
        })
    except Exception as e:
        log.info("watch cache: 作れないので全部送る (%s)", str(e)[:120])
        _watch_cache[call_key] = (h, "")
        return None
    name = c.get("name") or ""
    _watch_cache[call_key] = (h, name)
    log.info("watch cache: %s (%s tokens)", name, (c.get("usageMetadata") or {}).get("totalTokenCount"))
    return name or None


def _drop_watch_cache(call_key: str) -> None:
    got = _watch_cache.pop(call_key, None)
    if got and got[1]:
        try:
            _gemini_rest("DELETE", got[1])
        except Exception:
            pass  # TTL で消える


def drop_ended_watch_caches(active: set[str]) -> None:
    for k in [k for k in _watch_cache if k not in active]:
        _drop_watch_cache(k)


def make_watch_output(
    convo: str,
    caller: str | None,
    notes: list[str],
    frags: list[tuple[str, str, str]],
    lookup: str = "",
    identity: str = "",
    call_key: str = "",
) -> str:
    ctx = gather_workspace()
    notes_block = "\n".join(f"- {s}" for s in notes) if notes else "(なし)"
    frags_block = (
        json.dumps(
            [{"kind": k, "title": ti, "text": tx} for k, ti, tx in frags],
            ensure_ascii=False,
            indent=1,
        )
        if frags
        else "(なし)"
    )
    prefix = (
        f"# この電話について (管制室の設定)\n{identity or '(不明)'}\n\n"
        f"# ワークスペースの内容\n{ctx}\n\n"
    )
    rest = (
        f"# すでに電話AIへ送ったメモ\n{notes_block}\n\n"
        f"# 現在表示中の吹き出し (続投するものは一字一句同じで再掲)\n{frags_block}\n\n"
        f"# 番号のweb検索結果\n{lookup or '(なし)'}\n\n"
        f"# 進行中の通話の文字起こし (相手番号: {caller or '不明'})\n{convo}\n\n"
        "指定のJSON形式で出力。"
    )
    cache = _watch_cache_for(call_key, prefix) if call_key else None
    if cache:
        try:
            return call_gemini(WATCHER_PROMPT, rest, json_mode=True, cached=cache)
        except urllib.error.HTTPError as e:
            # キャッシュが消えていた等。次の発話で作り直す
            log.info("watch cache: 使えなかった (%s) — 全部送る", e.code)
            _watch_cache.pop(call_key, None)
    return call_gemini(WATCHER_PROMPT, prefix + rest, json_mode=True)


def urgent_allowed(number: str) -> bool:
    """この相手は緊急呼び出しを許可されているか。電話帳mdの `緊急呼び出し` を直接読む。

    ⚠**判定をLLMに委ねない。** プロンプトに「フラグを見て守れ」と書いても確率的に破られる。
      鳴らしてよい相手かはコードで決め、LLMには「用件が緊急か」だけを判断させる
      (hookdの門番と同じ二層の考え方)。

    ⚠**番号や名乗りから推測しない。** 日本では発信者番号の偽装が現在進行形の主要手口で、
      警察庁が「表示された番号を信用するな」と公式に注意喚起している (末尾0110の警察署番号を
      騙る詐欺)。着信側で検証する基盤 (STIR/SHAKEN等) が日本には無いため、
      信用できるのは**本人が管制室で明示的に許可した**という事実だけ。
    """
    if not re.fullmatch(r"[0-9]{4,20}", number or ""):
        # 非通知・通知不可能・+始まりなど。⚠キャリアが偽装を潰した結果がこの形なので、
        #   むしろ疑わしい印。鳴らす対象から常に外す
        return False
    p = phonebook_path(number)
    if not p or not p.is_file():
        return False
    try:
        m = re.search(r"^緊急呼び出し:\s*(\S+)\s*$", p.read_text(encoding="utf-8"), re.M)
    except Exception:
        return False
    return bool(m) and m.group(1).strip().lower() in ("true", "yes", "はい")


def fire_handoff(room: str, number: str, reason: str = "") -> None:
    """判断層からの取り次ぎ発火 (電話AIのツール呼び出しとのOR)。
    hookd側の /handoff_request は冪等 (未承諾の要求には合流するだけ) なので、
    電話AIと二重に叩いても呼び出しが延びたり二重に鳴ったりしない。

    ⚠reason は**本人のスマホの着信画面にそのまま出る**。固定文字列にしていた頃は
      「判断層: 相手が直接の対話を望んでいる」と内部用語が表示され、出るかどうかの
      判断材料にならなかった (2026-08-01に実機で発覚)。LLMに具体的に書かせる。"""
    q = urllib.parse.urlencode(
        {
            "room": room,
            "number": number,
            "reason": (reason or "").strip()[:120] or "直接お話ししたいとのご希望",
        }
    )
    with urllib.request.urlopen(f"{HOOKD_URL}/handoff_request?{q}", timeout=3) as r:
        r.read()


async def watch_conversations(pool: asyncpg.Pool) -> None:
    calls = await pool.fetch(
        "SELECT id, caller_number, room_name, answered_by FROM calls "
        "WHERE ended_at IS NULL AND started_at > now() - interval '2 hours'"
    )
    if _watch_cache:  # 終わった通話の明示キャッシュを消す (保存料がかかるので TTL まで待たない)
        await asyncio.get_running_loop().run_in_executor(
            None, drop_ended_watch_caches, {str(c["id"]) for c in calls}
        )
    for c in calls:
        cid = str(c["id"])
        if cid not in _watch_seen:
            _watch_seen[cid] = 0
            # 通話開始直後の1枚目: 下調べ (電話帳+直近通話+番号web検索) をAIが考察に凝縮して吹き出しに
            try:
                number = c["caller_number"] or ""
                intro_raw = await build_caller_context(pool, number)
                lk = await pool.fetchrow(
                    "SELECT result FROM agent_jobs WHERE kind='number_lookup' AND query=$1 "
                    "AND status='done' ORDER BY id DESC LIMIT 1",
                    number,
                )
                loop = asyncio.get_running_loop()
                try:
                    title, text = await loop.run_in_executor(
                        None, digest_intro, intro_raw, lk["result"] if lk else None, number
                    )
                except Exception:
                    log.exception("intro digest failed — 生テキストで代替")
                    title, text = "📇 相手の下調べ", intro_raw[:800]
                await pool.execute(
                    "INSERT INTO fragments (call_id, kind, title, text) "
                    "VALUES ($1, 'info', $2, $3)",
                    c["id"],
                    title,
                    text,
                )
            except Exception:
                log.exception("intro fragment failed (継続)")
        segs = await pool.fetch(
            "SELECT seq, speaker, text FROM transcript_segments WHERE call_id=$1 ORDER BY id",
            c["id"],
        )
        last_caller = max((s["seq"] for s in segs if s["speaker"] == "caller"), default=0)
        if last_caller <= _watch_seen[cid]:
            continue  # 相手の新しい発話が無ければLLMを呼ばない
        _watch_seen[cid] = last_caller
        notes = [
            r["result"]
            for r in await pool.fetch(
                "SELECT result FROM agent_jobs WHERE call_id=$1 AND kind='agent_note' ORDER BY id",
                c["id"],
            )
        ]
        current_rows = await pool.fetch(
            "SELECT id, kind, title, text FROM fragments WHERE call_id=$1 ORDER BY id", c["id"]
        )
        convo = "\n".join(
            f"{SPEAKER_LABEL.get(s['speaker'], s['speaker'])}: {s['text']}" for s in segs
        )
        loop = asyncio.get_running_loop()
        try:
            raw = await loop.run_in_executor(
                None,
                make_watch_output,
                convo,
                c["caller_number"],
                notes,
                [(r["kind"], r["title"], r["text"]) for r in current_rows],
                _lookup_text(c["caller_number"] or ""),
                await line_identity(pool),
                cid,
            )
            data = parse_json_object(raw)
        except Exception:
            log.exception("watch output failed (継続)")
            continue
        # 取り次ぎ判断 (OR発火の判断層側)。メモや吹き出しの処理より先に —
        # 本人の端末が鳴るまでの時間が一番効く。
        #
        # ⚠**本人がすでに通話に入っていたら鳴らさない** (2026-08-01に実機で発覚)。
        #   判断層は会話だけを見ているので「相手が本人と話したがっている」と判定し続け、
        #   本人が出て話している最中に取り次ぎを再発火して**鳴らし続けていた**。
        #   answered_by は operator 入室時に ai_then_human / human へ更新される。
        #   会話に user 発話があるかも併せて見る (更新前の隙間を埋める)。
        owner_present = str(c["answered_by"] or "") in ("human", "ai_then_human") or any(
            s["speaker"] == "user" for s in segs
        )
        # ⚠許可された相手かはコードで決める (urgent_allowed)。LLMは「用件が緊急か」だけ
        may_ring = urgent_allowed(c["caller_number"] or "")
        if data.get("handoff") is True and not may_ring:
            log.info(
                "handoff 判定は true だが未許可の相手なので鳴らさない (call %s..., %s)",
                cid[:8],
                c["caller_number"],
            )
        if data.get("handoff") is True and c["room_name"] and may_ring and not owner_present:
            try:
                reason = data.get("handoff_reason")
                await loop.run_in_executor(
                    None,
                    fire_handoff,
                    c["room_name"],
                    c["caller_number"] or "",
                    reason if isinstance(reason, str) else "",
                )
                log.info("handoff fired by watcher (call %s...)", cid[:8])
            except Exception:
                log.exception("watcher handoff failed (継続)")
        note = data.get("ai_note")
        note = note.strip() if isinstance(note, str) else ""
        if note and note.upper() not in ("NONE", "NULL"):
            await pool.execute(
                """INSERT INTO agent_jobs (call_id, kind, query, status, result, finished_at)
                   VALUES ($1, 'agent_note', $2, 'done', $3, now())""",
                c["id"],
                f"watch:{last_caller}",
                note,
            )
            # agent へ即時配達 (LISTEN agent_push) — 2秒ポーリング待ちを潰す
            await pool.execute("SELECT pg_notify('agent_push', $1)", cid)
            log.info("agent_note pushed (call %s...): %s", cid[:8], note[:100])
        # fragments はスナップショット (表示すべき完全なリスト) — title+text一致で差分適用。
        # 続投分はDBのidを保つ (UIで再アニメーションさせない)、消えた分はDELETE、新規はINSERT
        desired: list[tuple[str, str, str]] = []
        for f in (data.get("fragments") or [])[:10]:
            if isinstance(f, dict) and str(f.get("text", "")).strip():
                kind = str(f.get("kind", "info")).strip()[:20] or "info"
                title = str(f.get("title", "")).strip()[:40]
                desired.append((kind, title, str(f["text"]).strip()))
        desired_keys = {(ti, tx) for _, ti, tx in desired}
        existing_keys = {(r["title"], r["text"]) for r in current_rows}
        removed = [r for r in current_rows if (r["title"], r["text"]) not in desired_keys]
        added = [(k, ti, tx) for k, ti, tx in desired if (ti, tx) not in existing_keys]
        for r in removed:
            await pool.execute("DELETE FROM fragments WHERE id=$1", r["id"])
        for kind, title, text in added:
            await pool.execute(
                "INSERT INTO fragments (call_id, kind, title, text) VALUES ($1, $2, $3, $4)",
                c["id"],
                kind,
                title,
                text,
            )
        if removed or added:
            log.info(
                "fragments updated (call %s...): +%d -%d (計%d)",
                cid[:8],
                len(added),
                len(removed),
                len(desired),
            )
        elif not note:
            log.info("watch: 変更なし (call %s..., seq %s)", cid[:8], last_caller)


async def export_transcripts(pool: asyncpg.Pool) -> None:
    """終了済み通話を 通話記録/*.md に書き出す (存在チェックはroom名で)。"""
    rec_dir = WORKSPACE / "通話記録"
    rec_dir.mkdir(parents=True, exist_ok=True)
    calls = await pool.fetch(
        "SELECT id, room_name, caller_number, started_at, ended_at, recording_path FROM calls "
        "WHERE ended_at IS NOT NULL ORDER BY started_at DESC LIMIT 50"
    )
    existing = {p.stem.rsplit("_", 1)[-1] for p in rec_dir.glob("*.md")}
    for c in calls:
        suffix = c["room_name"].rsplit("_", 1)[-1]
        if suffix in existing:
            continue
        segs = await pool.fetch(
            "SELECT seq, speaker, text FROM transcript_segments WHERE call_id=$1 ORDER BY id",
            c["id"],
        )
        if not segs:
            continue
        started = c["started_at"].astimezone()  # ローカル時刻 (JST)
        caller = c["caller_number"] or "unknown"
        dur = int((c["ended_at"] - c["started_at"]).total_seconds())
        name = f"{started:%Y-%m-%d_%H%M}_{caller}_{suffix}.md"
        lines = [
            "---",
            f"caller: \"{caller}\"",
            f"started: {started:%Y-%m-%d %H:%M}",
            f"duration_sec: {dur}",
            f"room: {c['room_name']}",
            "tags: [フラグメント, 通話記録]",
            "---",
            "",
            f"# 通話記録 {started:%Y-%m-%d %H:%M} ({caller})",
            "",
        ]
        if c["recording_path"]:
            lines += [recordings.embed_line(c["recording_path"]), ""]
        for s in segs:
            label = SPEAKER_LABEL.get(s["speaker"], s["speaker"])
            lines.append(f"- **{label}**: {s['text']}")
        (rec_dir / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
        log.info("exported %s", name)
        # 電話帳mdが無い相手は雛形を自動作成 (相手ごとのメモリ。中身は人間/エージェントが育てる)。
        #
        # ⚠**テスト通話では作らない** (2026-08-01)。自動テストは毎回違うダミー番号を使うので、
        #   放っておくと `name: 不明` の空mdだけが増える (実際に86件中83件がこれになった)。
        #   空mdは知識ではなくノイズで、「既知の相手か」の判定も濁らせる。
        #   判定はルーム名 — シミュレータは `call_<番号>_sim<連番>` を使うので当て推量が要らない
        if c["room_name"].rsplit("_", 1)[-1].startswith("sim"):
            continue
        if pb.is_number(caller) and BOOK.file(caller) is None:
            BOOK.ensure(caller)
            log.info("phonebook created: %s", caller)


CALL_SUMMARY_PROMPT = """電話の文字起こしを読み、終話後に本人 ({OWNER}) のスマホの通知と通話履歴に出す
要約を作ります。読者は{OWNER}本人です。応対したのは AI のことも本人のこともあります。
- 1〜2文、60字程度まで。「誰が・何の用で・折り返しが要るか (期限があれば)」を先に
- 相手が名乗っていなければ「名乗らず」と書く。相手が何も言わずに切れたら「無言のまま切れた」
- caller_org: 相手が名乗った会社・団体名 (例「山田建設」)。言っていなければ空文字
- caller_person: 相手が名乗った個人の名前 (例「山田」「山田太郎」)。言っていなければ空文字。
  AI や本人の名前は入れない
- lookup_conflict: 「番号検索の結果」が付いていて、通話で分かった相手の正体 (名乗った会社・団体・
  立場) がそれと食い違うときだけ、「番号検索では〇〇、通話では△△と名乗った」の形で書く。
  食い違わない・判断できないときは空文字。個人名が加わっただけ (テナント → テナントの山田) は食い違いではない
- 挨拶・お礼・AIの受け答えの説明は書かない
出力JSON: {"summary": "...", "caller_org": "...", "caller_person": "...", "lookup_conflict": "..."}"""


async def summarize_ended_calls(pool: asyncpg.Pool) -> None:
    """終話した通話に要約を付ける (calls.summary_md)。AI 応対の着信は終話通知の本文、
    どの通話もアプリの履歴で展開したときの本文になる (2026-09-25、履歴は 2026-09-26)。

    ⚠先に直近1時間の AI 応対の着信 (終話通知を待っている) を片付ける。それ以外 (本人が話した通話・
      発信・過去の通話) は、通話中でないときに 1 回 1 本だけ遡る。会話の見張りと同じループなので、
      通話中に遡ると見張りが遅れる。
      ⚠通知を出すかは端末の状態 API が answered_by と終話時刻で絞っているので、ここで要約を
      作っても本人が話した通話や過去の通話の通知は出ない
    ⚠失敗や発話なしでも空文字を入れて打ち切る — NULL のままだと毎秒やり直す"""
    c = await pool.fetchrow(
        """SELECT id, caller_number, room_name, started_at, ended_at FROM calls
           WHERE ended_at IS NOT NULL AND summary_md IS NULL
             AND direction = 'inbound' AND answered_by = 'ai'
             AND ended_at > now() - interval '1 hour'
           ORDER BY ended_at LIMIT 1"""
    )
    if not c:
        c = await pool.fetchrow(
            """SELECT id, caller_number, room_name, started_at, ended_at FROM calls
               WHERE ended_at IS NOT NULL AND summary_md IS NULL
                 AND NOT EXISTS (SELECT 1 FROM calls WHERE ended_at IS NULL)
               ORDER BY ended_at DESC LIMIT 1"""
        )
    if not c:
        return
    summary, org, person, conflict = await summarize_call(pool, c)
    await pool.execute("UPDATE calls SET summary_md=$2 WHERE id=$1", c["id"], summary)
    log.info("call summary %s: %s", c["id"], summary[:80])
    try:
        update_phonebook_after_call(c, summary, org, person, conflict)
    except Exception:
        log.exception("phonebook update failed: %s", c["id"])
    label = caller_label(c["caller_number"] or "", org, person)
    if label:
        await pool.execute("UPDATE calls SET caller_label=$2 WHERE id=$1", c["id"], label)


def caller_label(number: str, org: str, person: str) -> str | None:
    """通話ごとの相手の呼び名 (calls.caller_label、2026-09-26)。電話帳の名前 (番号の持ち主) で足りるときは None。
    - 話した個人がいて、持ち主と別人 → 「山田太郎」
    - その人が電話帳と違う組織を名乗った → 「南山テスト建設 佐藤」
    - 個人は名乗らず、電話帳と違う組織を名乗った → 「南山テスト建設」
    ⚠電話帳の更新 (update_phonebook_after_call) の後に呼ぶ — 持ち主の名前が決まってから比べる"""
    p = phonebook_path(number)
    owner = pb.name_state(p.read_text(encoding="utf-8"))[0] if p else ""
    owner = "" if owner == "不明" else owner
    other_org = org if org and not pb.same_org(org, owner) else ""
    if person and pb.norm_person(person) != pb.norm_person(owner):
        return f"{other_org} {person}".strip()[:60]
    return other_org[:60] or None


async def summarize_call(pool: asyncpg.Pool, c) -> tuple[str, str, str, str]:
    """通話 1 本の (要約, 名乗った組織, 名乗った個人, 番号検索との食い違い)。
    ⚠infra/scripts/try_phonebook_update.py (テスト通話で電話帳の更新を試す外部の道具) からも呼ぶ"""
    segs = await pool.fetch(
        "SELECT speaker, text FROM transcript_segments WHERE call_id=$1 ORDER BY id", c["id"]
    )
    summary = ""
    org = ""
    person = ""
    conflict = ""
    if not any(s["speaker"] == "caller" and s["text"].strip() for s in segs):
        summary = "無言のまま切れた"
    else:
        convo = "\n".join(f"{SPEAKER_LABEL.get(s['speaker'], s['speaker'])}: {s['text']}" for s in segs)
        try:
            out = await asyncio.get_running_loop().run_in_executor(
                None, call_gemini, CALL_SUMMARY_PROMPT,
                f"# 発信者番号\n{c['caller_number']}\n\n"
                f"# 番号検索の結果\n{_lookup_text(c['caller_number'] or '') or '(なし)'}\n\n"
                f"# 文字起こし\n{convo}",
                True
            )
            j = json.loads(out)
            summary = str(j.get("summary") or "").strip()[:200]
            org = str(j.get("caller_org") or "").strip()[:40]
            person = str(j.get("caller_person") or "").strip()[:40]
            conflict = str(j.get("lookup_conflict") or "").strip()[:120]
        except Exception:
            log.exception("call summary failed: %s", c["id"])
    return summary, org, person, conflict


async def run_lookup(pool: asyncpg.Pool, job_id: int, number: str) -> None:
    t0 = time.monotonic()
    try:
        result = await asyncio.get_running_loop().run_in_executor(None, lookup_number, number)
        await pool.execute(
            "UPDATE agent_jobs SET status='done', result=$2, finished_at=now() WHERE id=$1",
            job_id,
            result,
        )
        log.info("lookup %s done in %.1fs: %s", number, time.monotonic() - t0, result[:100])
    except Exception as e:
        log.exception("lookup %s failed", number)
        await pool.execute(
            "UPDATE agent_jobs SET status='error', result=$2, finished_at=now() WHERE id=$1",
            job_id,
            str(e)[:500],
        )


async def lookup_loop(pool: asyncpg.Pool) -> None:
    """番号検索だけの見張り (2026-09-25)。着信画面の「番号検索中…」を置き換えるので待たせない。

    ⚠main のループに入れておくと、1秒おき・1件ずつの処理で先に入る caller_context の後ろに並び、
      会話監視の LLM 呼び出しにも詰まる。実測で投入から完了まで 4〜8秒のうち1〜3秒がこの待ちだった。
      0.3秒おきに拾い、拾った分は並行で走らせる。"""
    while True:
        try:
            jobs = await pool.fetch(
                """UPDATE agent_jobs SET status = 'running'
                   WHERE id IN (SELECT id FROM agent_jobs
                                WHERE status = 'pending' AND kind = 'number_lookup'
                                FOR UPDATE SKIP LOCKED)
                   RETURNING id, query"""
            )
            for j in jobs:
                asyncio.create_task(run_lookup(pool, j["id"], j["query"]))
        except Exception:
            log.exception("lookup loop error (継続)")
        await asyncio.sleep(0.3)


for _name in ("SYSTEM_PROMPT", "DIGEST_PROMPT", "WATCHER_PROMPT", "CALL_SUMMARY_PROMPT"):
    globals()[_name] = globals()[_name].replace("{OWNER}", OWNER_NAME)


async def main() -> None:
    log.info("workspace=%s backend=%s dsn=%s", WORKSPACE, resolve_backend(), DSN.split("@")[-1])
    if not WORKSPACE.exists():
        log.error("ワークスペースが見つかりません: %s", WORKSPACE)
        sys.exit(1)
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=5)
    asyncio.create_task(lookup_loop(pool))
    tick = 0
    while True:
        try:
            await process_jobs(pool)
            await summarize_ended_calls(pool)  # 終話通知の本文
            await watch_conversations(pool)  # 会話監視 (相手の新発話があった時だけLLM)
            if tick % 30 == 0:  # 30秒ごと
                await export_transcripts(pool)
                await recordings.import_raw(pool, WORKSPACE)
            if tick % 3600 == 600:  # 1時間ごと (起動直後は避ける)
                await recordings.apply_retention(pool, WORKSPACE)
                await recordings.forget_missing(pool, WORKSPACE)
                await refresh_lookup_names(pool)
                await asyncio.get_running_loop().run_in_executor(None, fill_phonebook_names)
                await asyncio.get_running_loop().run_in_executor(None, fill_phonebook_kana)
        except Exception:
            log.exception("loop error (継続)")
        tick += 1
        await asyncio.sleep(1)


for _n in ("WATCHER_PROMPT", "LOOKUP_PROMPT", "DIGEST_PROMPT", "KANA_PROMPT", "CALL_SUMMARY_PROMPT"):
    if _n in globals():
        _PROMPT_TAGS[globals()[_n]] = _n.removesuffix("_PROMPT").lower()


if __name__ == "__main__":
    asyncio.run(main())
