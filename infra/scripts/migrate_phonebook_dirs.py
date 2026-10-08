#!/usr/bin/env python3
"""電話帳を番号ごとのフォルダへ移す (2026-09-26、一度だけ)。

  連絡先/<番号>.md  →  連絡先/<番号>/<番号>.md
  「## 話した人」の「### <名前>」の塊 → 連絡先/<番号>/<名前>.md (人のファイル)
  機械の節 (番号検索・最近の用件・話した人・未整理) の見出しに「（自動）」を付ける

⚠読み書きするコードは新旧どちらの形も扱い、書くときに新しい形へ移す。これはその一括版で、
  やらなくても壊れはしない (触られていない番号がいつまでも旧形式で残るだけ)。
⚠既定はドライラン。実際に動かすには --apply。⚠_未使用/ は触らない。

実行 (VM上、リポジトリ直下から):
  python3 infra/scripts/migrate_phonebook_dirs.py          # 何が動くかだけ出す
  python3 infra/scripts/migrate_phonebook_dirs.py --apply
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "directory-agent"))
import phonebook as pb  # noqa: E402

WORKSPACE = Path(os.environ.get("FRAGMENT_WORKSPACE", "/srv/fragment/workspace"))
ROOT = WORKSPACE / "連絡先"


def split_people(number: str, md: str) -> tuple[str, dict[str, list[str]]]:
    """旧「## 話した人」の ### 塊を取り出す。@return (塊を除いた md, {名前: [行]})"""
    m = re.search(r"^## 話した人[^\n]*\n", md, re.M)
    if not m:
        return md, {}
    rest = md[m.end():]
    nxt = re.search(r"^## ", rest, re.M)
    body, tail = (rest[: nxt.start()], rest[nxt.start():]) if nxt else (rest, "")
    people: dict[str, list[str]] = {}
    cur = None
    for line in body.splitlines():
        h = re.match(r"^### (.+?)\s*$", line)
        if h:
            cur = h.group(1)
            people.setdefault(cur, [])
        elif cur and line.startswith("- "):
            people[cur].append(line)
    if not people:
        return md, {}
    return md[: m.start()] + tail.lstrip("\n"), people


def main() -> int:
    apply = "--apply" in sys.argv
    book = pb.Book(ROOT)
    olds = sorted(p for p in ROOT.glob("*.md") if pb.is_number(p.stem))
    print(f"旧形式 {len(olds)} 件{'' if apply else ' (ドライラン)'}")
    for old in olds:
        number = old.stem
        md, people = split_people(number, old.read_text(encoding="utf-8"))
        print(f"  {number} → {number}/{number}.md" + (f" + 人 {len(people)} 件 ({'・'.join(people)})" if people else ""))
        if not apply:
            continue
        new = book.dir(number) / f"{number}.md"
        if new.exists():
            print(f"    ⚠{new} が既にある。旧形式は残して飛ばす (手で見比べる)")
            continue
        new.parent.mkdir(parents=True, exist_ok=True)
        new.write_bytes(pb.normalize_headings(md).encode("utf-8"))
        old.unlink()
        for name, lines in people.items():
            for line in reversed(lines):  # 古い順に足すと、新しい順に並ぶ
                book.add_person_line(number, name, line)
        if people:
            md = new.read_text(encoding="utf-8")
            new.write_bytes(pb.set_section(md, "話した人", book.people_index(number)).encode("utf-8"))
    if not apply:
        print("\n実際に動かすには --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
