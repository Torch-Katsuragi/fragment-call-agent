# -*- coding: utf-8 -*-
"""電話帳 (ワークスペースの 連絡先/) のファイル操作 (2026-09-26 に番号ごとのフォルダへ)。

形:
  連絡先/<番号>/<番号>.md   … 番号の基本情報 (持ち主の名前・メモ・番号検索・最近の用件)
  連絡先/<番号>/<人>.md     … 組織の番号からかけてくる個人ごと (個人の番号なら作らない)
  ⚠ファイル名を番号と同じにしているのは、Obsidian の [[<番号>]] が場所によらず解決するから
    (旧形式 連絡先/<番号>.md へのリンクがそのまま生きる)

書き手の決まり (ユーザーと 2026-09-26 に決めた。連絡先/連絡先.md にも同じことを書いてある):
  1. AI はファイルに直接書かない。AI は事実 (組織名・個人名・用件・食い違い) を返すだけで、
     どのファイルのどこに書くかはこのモジュールが決める
  2. 「## メモ」は人 (と Claude) の場所で、ここからは触らない。見出しに「（自動）」が付いた節は
     機械の場所で、毎回まるごと書き直してよい
  3. 人のファイルは増やしすぎない。名前のそろえ方と既存の人との突き合わせはここでやり、
     同じ人か迷ったら作らずに「## 未整理（自動）」に積む
  4. 書き手は worker だけ (1 件ずつ順に)。管制室が書くのは名前・読みがな・緊急呼び出しの許可だけ

旧形式 (連絡先/<番号>.md) は読むときにも見るし、書くときに新しい形へ移す。
"""

import re
from pathlib import Path

AUTO = "（自動）"

NUMBER_TEMPLATE = """---
number: "{number}"
name: 不明
tags: [フラグメント, 電話帳]
---

# {number}

## メモ

(まだ情報なし。名前・関係・話し方の注意などをここに書く)

## 最近の用件{auto}

"""

PERSON_TEMPLATE = """---
name: {name}
number: "{number}"
tags: [フラグメント, 電話帳, 人]
---

# {name} ([[{number}]])

## メモ

(この人について人が書く欄)

## 用件{auto}

"""


def is_number(s: str) -> bool:
    return bool(re.fullmatch(r"[+0-9]{4,20}", s or ""))


class Book:
    def __init__(self, root: Path):
        self.root = root  # ワークスペースの 連絡先/

    # ---- 場所 ----
    def dir(self, number: str) -> Path:
        return self.root / number

    def file(self, number: str) -> Path | None:
        """読むときの場所。新しい形が無ければ旧形式。どちらも無ければ None"""
        if not is_number(number):
            return None
        new = self.dir(number) / f"{number}.md"
        if new.exists():
            return new
        old = self.root / f"{number}.md"
        return old if old.exists() else None

    def ensure(self, number: str) -> Path | None:
        """書くときの場所。旧形式があれば新しい形へ移し、どちらも無ければ雛形で作る"""
        if not is_number(number):
            return None
        new = self.dir(number) / f"{number}.md"
        if new.exists():
            return new
        new.parent.mkdir(parents=True, exist_ok=True)
        old = self.root / f"{number}.md"
        if old.exists():
            new.write_text(normalize_headings(old.read_text(encoding="utf-8")), encoding="utf-8")
            old.unlink()
        else:
            new.write_text(NUMBER_TEMPLATE.format(number=number, auto=AUTO), encoding="utf-8")
        return new

    def numbers(self) -> list[str]:
        """電話帳にある番号の全部 (新旧どちらの形でも)。⚠_未使用/ と 連絡先.md は数えない"""
        out = set()
        for p in self.root.iterdir() if self.root.exists() else []:
            if p.is_dir() and is_number(p.name) and (p / f"{p.name}.md").exists():
                out.add(p.name)
            elif p.suffix == ".md" and is_number(p.stem):
                out.add(p.stem)
        return sorted(out)

    def people(self, number: str) -> list[Path]:
        d = self.dir(number)
        if not d.is_dir():
            return []
        return sorted(p for p in d.glob("*.md") if p.stem != number)

    # ---- 人 ----
    def find_person(self, number: str, person: str) -> tuple[str, Path | list[Path]]:
        """("match", path) / ("upgrade", path) / ("new", path) / ("ambiguous", [候補])。
        そろえ方 (敬称と空白を外して比べる):
          完全一致                                   → その人
          名乗りの方が短い (「山田」、既存「山田太郎」) → その人。2 人以上いれば迷う
          既存の方が短い (既存「山田」、名乗り「山田太郎」) → その人をフルネームに改める (upgrade)。
            ⚠既存がすでにフルネームで別の名前 (「山田花子」) なら別人として新しく作る
            (前の版は頭の一致だけで寄せていて、太郎と花子を同じ人にまとめた)"""
        norm = norm_person(person)
        existing = self.people(number)
        exact = [p for p in existing if norm_person(p.stem) == norm]
        if len(exact) == 1:
            return "match", exact[0]
        longer = [p for p in existing if norm_person(p.stem).startswith(norm)]
        if len(longer) == 1:
            return "match", longer[0]
        if len(longer) > 1:
            return "ambiguous", longer
        shorter = [p for p in existing if norm.startswith(norm_person(p.stem))]
        if len(shorter) == 1:
            return "upgrade", shorter[0]
        if len(shorter) > 1:
            return "ambiguous", shorter
        return "new", self.dir(number) / f"{file_safe(norm)}.md"

    def add_person_line(self, number: str, person: str, line: str) -> str:
        """人のファイルの「用件（自動）」に 1 行足す。@return match / new / ambiguous"""
        kind, target = self.find_person(number, person)
        if kind == "ambiguous":
            names = "・".join(p.stem for p in target)
            p = self.ensure(number)
            md = add_to_section(
                p.read_text(encoding="utf-8"), "未整理",
                f"{line} (「{person}」と名乗った。候補: {names})", limit=20,
            )
            p.write_text(md, encoding="utf-8")
            return kind
        path = target
        if kind == "upgrade":
            # 名字だけの人をフルネームに改める (ファイル名・name・見出し)。メモと用件はそのまま
            full = file_safe(norm_person(person))
            new_path = path.with_name(f"{full}.md")
            md = path.read_text(encoding="utf-8")
            md = re.sub(r"^name:.*$", lambda _: f"name: {full}", md, count=1, flags=re.M)
            md = re.sub(rf"^# {re.escape(path.stem)} ", f"# {full} ", md, count=1, flags=re.M)
            new_path.write_text(md, encoding="utf-8")
            path.unlink()
            path = new_path
        if kind == "new":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                PERSON_TEMPLATE.format(name=path.stem, number=number, auto=AUTO), encoding="utf-8"
            )
        md = add_to_section(path.read_text(encoding="utf-8"), "用件", line, limit=10)
        path.write_text(md, encoding="utf-8")
        return kind

    def people_index(self, number: str) -> str:
        """番号のファイルに載せる「話した人」の一覧 (人ごとに最新の 1 行)"""
        lines = []
        for p in self.people(number):
            last = section_items(p.read_text(encoding="utf-8"), "用件")
            tail = f" — {last[0].removeprefix('- ')}" if last else ""
            lines.append(f"- [[{number}/{p.stem}|{p.stem}]]{tail}")
        return "\n".join(lines)


# ---- 名前 ----
def norm_person(name: str) -> str:
    n = re.sub(r"\s+", "", name or "")
    return re.sub(r"(さん|様|さま|氏|くん|君|ちゃん)$", "", n)


def file_safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|#\[\]\r\n]', "", name)[:40] or "名前なし"


def name_state(md: str) -> tuple[str, str]:
    """(name, name_source)。name_source が無い = 人が書いた名前 (または不明)"""
    m = re.search(r"^name:\s*[\"']?(.+?)[\"']?\s*$", md, re.M)
    src = re.search(r"^name_source:\s*(.+?)\s*$", md, re.M)
    return (m.group(1).strip() if m else ""), (src.group(1).strip() if src else "")


def set_name(md: str, name: str, source: str) -> str:
    name = re.sub(r"[\r\n\"]", " ", name).strip()[:60]
    md = re.sub(r"^name:.*$", lambda _: f"name: {name}", md, count=1, flags=re.M)
    if re.search(r"^name_source:", md, re.M):
        return re.sub(r"^name_source:.*$", lambda _: f"name_source: {source}", md, count=1, flags=re.M)
    return re.sub(r"^(name:.*)$", lambda m: f"{m.group(1)}\nname_source: {source}", md, count=1, flags=re.M)


# ---- 節 ----
def _heading(title: str) -> re.Pattern:
    # 旧形式の見出し (「（自動）」なし) も同じ節として読む
    return re.compile(rf"^## {re.escape(title)}(?:{re.escape(AUTO)})?[ \t]*\n", re.M)


def _split(md: str, title: str):
    m = _heading(title).search(md)
    if not m:
        return None
    rest = md[m.end():]
    nxt = re.search(r"^## ", rest, re.M)
    body, tail = (rest[: nxt.start()], rest[nxt.start():]) if nxt else (rest, "")
    return md[: m.start()], body, tail


def section_items(md: str, title: str) -> list[str]:
    s = _split(md, title)
    return [l for l in s[1].splitlines() if l.startswith("- ")] if s else []


def set_section(md: str, title: str, body: str, before: str = "最近の用件", auto: bool = True) -> str:
    """節を書き直す (無ければ before の節の前に作る。before も無ければ末尾)"""
    head = f"## {title}{AUTO if auto else ''}\n"
    block = head + "\n" + body.strip("\n") + "\n\n" if body.strip() else ""
    s = _split(md, title)
    if s:
        pre, _, tail = s
        return pre + block + tail.lstrip("\n")
    if not block:
        return md
    b = _heading(before).search(md)
    if b:
        return md[: b.start()] + block + md[b.start():]
    return md.rstrip("\n") + "\n\n" + block


def add_to_section(md: str, title: str, line: str, limit: int = 10) -> str:
    """節の先頭に 1 行足す (同じ行は重ねない。新しい順に limit 行まで)"""
    items = section_items(md, title)
    if line in items:
        return md
    return set_section(md, title, "\n".join([line] + items[: limit - 1]))


def same_org(a: str, b: str) -> bool:
    """組織名が同じとみなせるか。空白を外して一致、または片方がもう片方を含む
    (「山田建設株式会社」と「山田建設」)。⚠文字起こしの揺れ (工務店/公務店) は同じとみなさない —
    それも食い違いとして人に見せる"""
    x, y = re.sub(r"\s+", "", a or ""), re.sub(r"\s+", "", b or "")
    return bool(x and y) and (x == y or x in y or y in x)


def note_conflict(md: str, line: str) -> str:
    """「## 食い違い（自動）」に ⚠ の行を足す (同じ行は重ねない。新しい順に 20 行まで)"""
    return add_to_section(md, "食い違い", line, limit=20)


def normalize_headings(md: str) -> str:
    """旧形式の機械の節に「（自動）」を付ける。「話した人」の ### 見出しは移行スクリプトが人のファイルへ分ける"""
    for t in ("番号検索", "最近の用件", "話した人", "未整理", "食い違い"):
        md = re.sub(rf"^## {t}[ \t]*$", f"## {t}{AUTO}", md, flags=re.M)
    return md
