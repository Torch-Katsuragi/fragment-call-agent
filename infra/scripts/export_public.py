# -*- coding: utf-8 -*-
"""公開用リポジトリを書き出す (2026-09-25)。

作業用リポジトリ (非公開) には運用の実値 — GCP プロジェクト名・ドメイン・手元のパス・番号など — が
残っている。これらはデプロイ (git archive で VM へ送る) が使っているので、ソースからは消さない。
代わりに公開するときだけ、この書き出しで置き換える。

  python infra/scripts/export_public.py [--ref HEAD] [--out ../Call-Agent-public]

- 置き換え表と禁止語は infra/scripts/publish-map.local (⚠git 管理外。実値が書いてあるため)
    置き換え:  <実値><TAB>=><TAB><公開用の値>     (文字列そのまま。上から順に当てる)
    禁止語:    !deny<TAB><正規表現>                (書き出し後に1つでも残っていたら止める)
    除外:      !exclude<TAB><パスの先頭>            (公開物に入れないファイル・ディレクトリ)
- 書き出し先には、履歴1件だけの新しい git リポジトリを作る (作業用の履歴は出さない —
  日付入りの履歴は先使用の証拠として手元に残す)。push はしない
"""
import argparse
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAP = Path(__file__).with_name("publish-map.local")


def load_map() -> tuple[list[tuple[str, str]], list[str], list[str]]:
    if not MAP.exists():
        sys.exit(f"置き換え表がありません: {MAP}")
    repl, deny, excl = [], [], []
    for raw in MAP.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        if raw.startswith("!deny\t"):
            deny.append(raw.split("\t", 1)[1])
        elif raw.startswith("!exclude\t"):
            excl.append(raw.split("\t", 1)[1])
        elif "\t=>\t" in raw:
            a, b = raw.split("\t=>\t", 1)
            repl.append((a, b))
        else:
            sys.exit(f"読めない行: {raw!r}")
    return repl, deny, excl


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--out", default=str(ROOT.parent / "Call-Agent-public"))
    args = ap.parse_args()
    out = Path(args.out).resolve()
    if out == ROOT or ROOT in out.parents:
        sys.exit("書き出し先を作業用リポジトリの中にしない")
    repl, deny, excl = load_map()

    tar = subprocess.run(["git", "archive", args.ref], cwd=ROOT, capture_output=True, check=True).stdout
    if out.exists():
        # ⚠Windows では .git/objects が読み取り専用で、そのままでは消せない
        def _force(func, path, _exc):
            os.chmod(path, stat.S_IWRITE)
            func(path)

        shutil.rmtree(out, onerror=_force)
    out.mkdir(parents=True)
    changed = 0
    with tarfile.open(fileobj=io.BytesIO(tar)) as tf:
        for m in tf.getmembers():
            if not m.isfile() or any(m.name == e or m.name.startswith(e.rstrip("/") + "/") for e in excl):
                continue
            data = tf.extractfile(m).read()
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = None
            if text is not None:
                new = text
                for a, b in repl:
                    new = new.replace(a, b)
                changed += new != text
                data = new.encode("utf-8")
            dst = out / m.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            if m.mode & 0o111:
                dst.chmod(0o755)
    print(f"書き出し: {out} (置き換えたファイル {changed})")

    hits = []
    for p in out.rglob("*"):
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pat in deny:
            for i, line in enumerate(text.splitlines(), 1):
                if re.search(pat, line):
                    hits.append(f"{p.relative_to(out)}:{i}: [{pat}] {line.strip()[:100]}")
    if hits:
        print(f"⚠禁止語が {len(hits)} 件残っている — 置き換え表を足すかソースを直す:")
        print("\n".join(hits[:50]))
        sys.exit(1)
    print("禁止語: 0 件")

    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=out, check=True)
    subprocess.run(["git", "add", "-A"], cwd=out, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "Initial public release"], cwd=out, check=True)
    print("公開用リポジトリを作成 (履歴1件、push はしていない)")


if __name__ == "__main__":
    main()
