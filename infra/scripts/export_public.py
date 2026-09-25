# -*- coding: utf-8 -*-
"""公開リポジトリ (fragment-call-agent) へ出す (2026-09-25)。

作業用リポジトリ (非公開) には運用の実値 — GCP プロジェクト名・ドメイン・手元のパス・番号など — が
残っている。これらはデプロイ (git archive で VM へ送る) が使っているので、ソースからは消さない。
代わりに公開するときだけ、この書き出しで置き換える。作業用の履歴は出さない
(日付入りの履歴は先使用の証拠として手元に残す)。

■ 更新 (ふだんはこれ)
    python infra/scripts/export_public.py -m "着信の3択を追加"
    git -C ../Call-Agent-public push          # 送るのは手で (内容を git show で見てから)

  1. 公開側の手元のコピー (--public、既定 ../Call-Agent-public) が未コミットの変更なしで
     GitHub と揃っていることを確かめる (fetch → fast-forward)
  2. 一時フォルダに書き出し、置き換え表を当て、禁止語を検査する (1つでも残っていたら止める)
  3. 手元のコピーへ写す (消えたファイルは消す) → 差分だけを1コミットにする。push はしない
  ⚠コミットメッセージは -m で毎回書く。作業用のコミットメッセージは流用しない (氏名等が入っていることがある)

■ 新規 (最初の1回だけ。公開済みの今は使わない)
    python infra/scripts/export_public.py --fresh --public ../somewhere-new
  ⚠GitHub とつながった (origin がある) フォルダには使えない — 丸ごと作り直して履歴を消すため

■ 置き換え表と禁止語は infra/scripts/publish-map.local (⚠git 管理外。実値が書いてあるため)
    置き換え:  <実値><TAB>=><TAB><公開用の値>     (文字列そのまま。上から順に当てる)
    禁止語:    !deny<TAB><正規表現>
    除外:      !exclude<TAB><パスの先頭>            (公開物に入れないファイル・ディレクトリ)
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
import tempfile
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


def _rmtree(path: Path) -> None:
    # ⚠Windows では .git/objects が読み取り専用で、そのままでは消せない
    def _force(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    shutil.rmtree(path, onerror=_force)


def git(cwd: Path, *args: str, capture: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=capture, text=True, encoding="utf-8")
    if r.returncode != 0:
        sys.exit(f"git {' '.join(args)} に失敗 ({cwd}):\n{r.stderr or ''}")
    return r.stdout or ""


def export_tree(ref: str, out: Path) -> None:
    """ref を out に書き出し、置き換えを当て、禁止語を検査する (残っていたら止める)"""
    repl, deny, excl = load_map()
    tar = subprocess.run(["git", "archive", ref], cwd=ROOT, capture_output=True, check=True).stdout
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
    print(f"書き出し: {ref} (置き換えたファイル {changed})")

    hits = []
    for p in out.rglob("*"):
        if not p.is_file() or ".git" in p.relative_to(out).parts:
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


def mirror(src: Path, dst: Path) -> None:
    """dst (git の作業ツリー) を src と同じ中身にする。.git には触らない"""
    for p in sorted(dst.rglob("*"), reverse=True):
        rel = p.relative_to(dst)
        if rel.parts[0] == ".git":
            continue
        if p.is_file() and not (src / rel).exists():
            p.unlink()
        elif p.is_dir() and not any(p.iterdir()):
            p.rmdir()
    for p in src.rglob("*"):
        if p.is_file():
            d = dst / p.relative_to(src)
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, d)


def update(ref: str, public: Path, message: str) -> None:
    if not (public / ".git").exists():
        sys.exit(f"公開側の手元のコピーがありません: {public} (git clone してから)")
    if git(public, "status", "--porcelain").strip():
        sys.exit(f"公開側に未コミットの変更がある: {public}")
    git(public, "fetch", "-q", "origin")
    git(public, "merge", "-q", "--ff-only", "@{u}")
    with tempfile.TemporaryDirectory() as tmp:
        export_tree(ref, Path(tmp))
        mirror(Path(tmp), public)
    git(public, "add", "-A")
    stat_out = git(public, "diff", "--cached", "--stat")
    if not stat_out.strip():
        print("公開側と差分なし — コミットしない")
        return
    print(stat_out)
    git(public, "commit", "-q", "-m", message)
    print(f"コミットした (push はしていない): git -C {public} show --stat HEAD で確かめて git -C {public} push")


def fresh(ref: str, out: Path) -> None:
    if (out / ".git").exists() and git(out, "remote").strip():
        sys.exit(f"{out} は GitHub とつながっている — --fresh は使わない (更新は -m で)")
    if out.exists():
        _rmtree(out)
    out.mkdir(parents=True)
    export_tree(ref, out)
    git(out, "init", "-q", "-b", "main")
    git(out, "add", "-A")
    git(out, "commit", "-q", "-m", "Initial public release")
    print(f"新規リポジトリを作成 (履歴1件、push はしていない): {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--public", default=str(ROOT.parent / "Call-Agent-public"))
    ap.add_argument("-m", "--message", help="公開側のコミットメッセージ (更新のとき必須)")
    ap.add_argument("--fresh", action="store_true", help="最初の1回だけ: 履歴1件の新規リポジトリを作る")
    args = ap.parse_args()
    public = Path(args.public).resolve()
    if public == ROOT or ROOT in public.parents:
        sys.exit("公開側を作業用リポジトリの中にしない")
    if args.fresh:
        fresh(args.ref, public)
    else:
        if not args.message:
            sys.exit("-m でコミットメッセージを付ける (作業用のメッセージは流用しない)")
        update(args.ref, public, args.message)


if __name__ == "__main__":
    main()
