#!/usr/bin/env python3
"""育っていない電話帳mdを片付ける (2026-08-01)。

なぜ要るか:
  通話が終わるたびに `連絡先/<番号>.md` の雛形が自動生成されていたので、**自動テストの
  ダミー番号だけで86件中83件が `name: 不明` の空md**になった。空mdは知識ではなくノイズで、
  「既知の相手か」の判定も濁らせる (2026-08-01に実際に判定を厳しくする原因になった)。
  発生源は worker.py 側で塞いだ。これは既に溜まった分の後始末。

⚠**既定はドライラン**。実際に動かすには `--apply` を付ける。
⚠削除ではなく `連絡先/_未使用/` へ**移動**する。誤って消したときに戻せる形にしておく
  (ワークスペースを Google Drive 等と双方向同期している場合、消すと両側から消える)。
⚠移動の条件は3つとも満たすものだけ:
   ① `name:` が 不明 か空          — 名前が入っていれば本人かエージェントが育てている
   ② メモが雛形の文言のまま        — 一文字でも書き足されていれば残す
   ③ 「最近の用件」以降が空        — 用件が書かれていれば残す

実行 (VM上、リポジトリ直下から):
  python3 infra/scripts/prune_phonebook.py          # 何が動くかだけ出す
  python3 infra/scripts/prune_phonebook.py --apply  # 実際に移動する
"""

import os
import re
import sys
from pathlib import Path

WORKSPACE = Path(
    os.environ.get("FRAGMENT_WORKSPACE", "/srv/fragment/workspace")
)
BOOK = WORKSPACE / "連絡先"
ATTIC = BOOK / "_未使用"

PLACEHOLDER = "(まだ情報なし。名前・関係・話し方の注意などをここに書く)"


def is_untouched(text: str) -> tuple[bool, str]:
    """雛形のまま育っていないか。育っている理由が1つでもあれば False を返す。"""
    m = re.search(r"^name:\s*(.*)$", text, re.M)
    name = (m.group(1).strip() if m else "").strip("\"'")
    if name and name != "不明":
        return False, f"名前がある ({name})"

    # メモ欄: 雛形の一文以外に本文があれば育っている
    memo = re.search(r"^## メモ\s*$(.*?)^## ", text, re.M | re.S)
    body = (memo.group(1) if memo else "").replace(PLACEHOLDER, "").strip()
    if body:
        return False, f"メモが書かれている ({body[:20]}…)"

    # 「最近の用件」以降
    recent = text.split("## 最近の用件", 1)
    if len(recent) > 1 and recent[1].strip():
        return False, f"用件が書かれている ({recent[1].strip()[:20]}…)"

    # ⚠緊急呼び出しの許可は本人が明示的に立てたもの。名前が無くても絶対に触らない
    if re.search(r"^緊急呼び出し:", text, re.M):
        return False, "緊急呼び出しが設定されている"

    return True, ""


def main() -> int:
    apply = "--apply" in sys.argv
    if not BOOK.exists():
        print(f"電話帳が見つかりません: {BOOK}")
        return 1

    files = sorted(p for p in BOOK.glob("*.md") if p.is_file())
    move, keep = [], []
    for p in files:
        untouched, why = is_untouched(p.read_text(encoding="utf-8"))
        (move if untouched else keep).append((p, why))

    print(f"電話帳 {len(files)}件 → 残す {len(keep)}件 / 片付ける {len(move)}件\n")
    print("--- 残すもの ---")
    for p, why in keep:
        print(f"  {p.name}  ({why})")
    print(f"\n--- {'移動する' if apply else '移動する予定 (ドライラン)'} ---")
    for p, _ in move:
        print(f"  {p.name}")

    if not apply:
        print(f"\n実際に動かすには --apply を付ける。移動先: {ATTIC}")
        return 0

    ATTIC.mkdir(parents=True, exist_ok=True)
    for p, _ in move:
        p.rename(ATTIC / p.name)
    print(f"\n{len(move)}件を {ATTIC} へ移動した (削除ではないので戻せる)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
