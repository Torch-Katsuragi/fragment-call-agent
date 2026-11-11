# -*- coding: utf-8 -*-
"""ワークスペース (/fragment) 上の設定プロンプト読み込みと緊急呼び出し許可判定の共通部品。

agent.py (Cloud Run / ワーカー) と calld.py (HTTP ディスパッチャ) の双方から参照し、
プロンプトのセクション定義・YAML frontmatter 除去・電話帳の緊急呼び出し判定を単一実装に保つ。
"""

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger("call-agent")

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
}

_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\r?\n(?:.*?\r?\n)?---[ \t]*(?:\r?\n|\Z)", re.DOTALL
)


def strip_frontmatter(md: str) -> str:
    """先頭の YAML frontmatter (--- ... ---、LF/CRLF 両対応) があれば除去して .strip() を返す。"""
    if not md:
        return ""
    return _FRONTMATTER_RE.sub("", md, count=1).strip()


def load_workspace_prompts(workspace: str | Path | None = None) -> dict[str, str]:
    """ワークスペース配下の各設定プロンプトを読み込み、非空のセクションだけ辞書で返す。
    calld.py が Cloud Run への HTTP ペイロード (prompts) を組み立てる際にも使う。"""
    ws_raw = workspace if workspace is not None else os.environ.get("FRAGMENT_WORKSPACE", "")
    if not ws_raw:
        return {}
    ws_path = Path(ws_raw)
    out: dict[str, str] = {}
    for key, spec in PROMPT_SECTIONS.items():
        p = ws_path / spec["workspace"]
        try:
            text = strip_frontmatter(p.read_text(encoding="utf-8"))
            if text:
                out[key] = text
        except FileNotFoundError:
            pass
        except Exception:
            logger.exception("prompt read failed: %s", p)
    return out


def load_prompt_section(
    key: str,
    overrides: dict[str, str] | None = None,
    workspace: str | Path | None = None,
    defaults_dir: Path | None = None,
) -> str:
    """プロンプトのセクションを読む。HTTP リクエストの overrides があればそれを優先し、
    無ければワークスペース (/fragment) → リポジトリの既定の順に読む。
    通話ごとに読むので、mdを直せば次の通話から効く (再起動不要)。"""
    if overrides and overrides.get(key):
        text = strip_frontmatter(overrides[key])
        if text:
            return text
    spec = PROMPT_SECTIONS[key]
    ws_raw = workspace if workspace is not None else os.environ.get("FRAGMENT_WORKSPACE", "")
    if ws_raw:
        p = Path(ws_raw) / spec["workspace"]
        try:
            if p.is_file():
                text = strip_frontmatter(p.read_text(encoding="utf-8"))
                if text:
                    return text
                logger.warning("プロンプト %s が空 (%s) — 既定を使う", key, p)
        except Exception:
            logger.exception("プロンプト %s を読めない (%s) — 既定を使う", key, p)
    ddir = defaults_dir if defaults_dir is not None else PROMPTS_DIR
    try:
        return strip_frontmatter((ddir / spec["default"]).read_text(encoding="utf-8"))
    except Exception:
        logger.exception("既定のプロンプト %s も読めない — このセクション無しで続行", key)
        return ""


def urgent_allowed(number: str | None, workspace: str | Path | None = None) -> bool:
    """この相手は緊急呼び出し (本人の携帯を鳴らすこと) を許可されているか。
    実体はワークスペースの電話帳md `連絡先/<番号>/<番号>.md` (または旧形式 `連絡先/<番号>.md`) の
    `緊急呼び出し` プロパティで、**本人が管制室で承認したときだけ** true になる。

    ⚠番号や名乗りから推測してはいけない。日本では発信者番号の偽装が現在進行形の主要手口で、
      警察庁が「表示された番号を信用するな」と公式に注意喚起している (末尾0110の警察署番号を
      騙る詐欺・被害100億円超)。着信側で検証する基盤が日本には無いので、
      信用できるのは本人の明示的な承認だけ。⚠非通知・+始まりは常に不許可
      (キャリアが偽装を潰した結果がその形なので、むしろ疑わしい印)。
    """
    if not re.fullmatch(r"[0-9]{4,20}", number or ""):
        return False
    if workspace is not None:
        ws = Path(workspace) if str(workspace) else None
    else:
        ws = (
            Path(os.environ["FRAGMENT_WORKSPACE"])
            if os.environ.get("FRAGMENT_WORKSPACE")
            else None
        )
    if ws is None:
        return False
    # 2026-09-26 から 連絡先/<番号>/<番号>.md。移し替えの途中でも効くよう旧形式も見る
    book = ws / "連絡先"
    text = None
    for p in (book / number / f"{number}.md", book / f"{number}.md"):
        try:
            text = p.read_text(encoding="utf-8")
            break
        except Exception:
            continue
    if text is None:
        return False
    m = re.search(r"^緊急呼び出し:[ \t]*(\S+)[ \t]*\r?$", text, re.M)
    return bool(m) and m.group(1).strip().lower() in ("true", "yes", "はい")
