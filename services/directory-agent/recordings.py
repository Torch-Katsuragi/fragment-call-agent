# -*- coding: utf-8 -*-
"""通話の録音をワークスペース (Vault) へ移す・消す (2026-10-02)。

なぜ:
  録音は VM のディスク (data/recordings) に wav で溜まるだけで、聞くには VM に入るしかなかった。
  ユーザーの方針「普通に Vault 内に置けばよい。管制室の通話履歴から聞けるように」。
  ワークスペースは rclone で Drive と同期しているので、ここに置けば Obsidian からも聞ける。

形:
  Asterisk の MixMonitor が 1 通話 3 本 (混合 / _rx=相手 / _tx=こちら) の 8kHz wav を書く。
  それを **左=相手・右=こちら (AI か本人) のステレオ Ogg Opus 1 本**にまとめる。
  話者を分けたまま 1 本になり、サイズは約 1/10 (8kHz wav 約 1MB/分 ×3 → 約 0.25MB/分)。
  置き場は `録音/YYYY-MM/<日時JST>_<番号>.ogg`。通話記録 md には埋め込みを足す。

  ⚠消すのはユーザー (2026-10-02)。管制室の設定で「◯日より古いものを自動で消す」(既定オフ) と
    「期間を指定して消す」。録音だけを消し、文字起こしと通話記録 md は残す (md の埋め込みは外す)。
"""

import json
import logging
import os
import re
import subprocess
import time
import wave
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("directory-agent")

JST = timezone(timedelta(hours=9))
# Asterisk の録音置き場 (compose で ../data/recordings をマウント)
RAW_DIR = Path(os.environ.get("RECORDING_RAW_DIR", "/recordings"))
REC_DIR_NAME = "録音"
# 生の wav の名前: <YYYYmmdd_HHMMSS (JST)>_<in|out>_<番号>_<UNIQUEID>[_rx|_tx].wav (extensions.conf)
_RAW_RE = re.compile(r"^(\d{8}_\d{6})_(in|out)_([^_]*)_(.+)$")
# 変換後の名前の頭 (JST の日時)。期間指定と保存期間はここから日付を取る
_OUT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_")
# まだ書き込み中かもしれない録音には触らない (MixMonitor は通話中ずっと書き続ける)
SETTLE_SEC = 60
# 一度失敗した録音を毎回やり直さない (壊れた wav でループしない)。プロセスが替われば再挑戦
_failed: set[str] = set()


def rec_root(workspace: Path) -> Path:
    return workspace / REC_DIR_NAME


def _wav_seconds(p: Path) -> float:
    with wave.open(str(p)) as w:
        return w.getnframes() / float(w.getframerate() or 8000)


def _encode(files: dict[str, Path], out: Path) -> None:
    """生の wav を Ogg Opus 1 本にする。rx/tx が揃っていればステレオ、無ければ混合をモノラルで"""
    tmp = out.with_suffix(".tmp.ogg")
    rx, tx = files.get("rx"), files.get("tx")
    if rx and tx:
        # ⚠rx と tx は長さが少しずれる。amerge は短い方で切れるので、長い方に揃えて無音で埋める
        dur = max(_wav_seconds(rx), _wav_seconds(tx))
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(rx), "-i", str(tx),
            "-filter_complex",
            f"[0:a]apad=whole_dur={dur:.3f}[a];[1:a]apad=whole_dur={dur:.3f}[b];[a][b]amerge=inputs=2[s]",
            "-map", "[s]", "-c:a", "libopus", "-b:a", "32k", "-application", "voip", str(tmp),
        ]
    else:
        src = files.get("mix") or rx or tx
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
            "-c:a", "libopus", "-b:a", "24k", "-application", "voip", str(tmp),
        ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError((r.stderr or "ffmpeg failed").strip()[:300])
    tmp.replace(out)


def _md_for_room(workspace: Path, room: str) -> Path | None:
    suffix = room.rsplit("_", 1)[-1]
    hits = sorted((workspace / "通話記録").glob(f"*_{suffix}.md"))
    return hits[0] if hits else None


def embed_line(rel: str) -> str:
    # Obsidian の埋め込み。ファイル名だけで引ける (録音の名前は Vault 内で一意)
    return f"![[{Path(rel).name}]]"


def link_in_md(workspace: Path, room: str, rel: str) -> None:
    """通話記録 md の見出しの直後に録音の埋め込みを足す (既にあれば何もしない)"""
    p = _md_for_room(workspace, room)
    if not p:
        return  # md はまだ無い — export_transcripts が書くときに入れる
    text = p.read_text(encoding="utf-8")
    line = embed_line(rel)
    if line in text:
        return
    lines = text.split("\n")
    for i, l in enumerate(lines):
        if l.startswith("# 通話記録"):
            lines[i + 1:i + 1] = ["", line]
            break
    else:
        lines += ["", line]
    p.write_text("\n".join(lines), encoding="utf-8")


def unlink_in_md(workspace: Path, room: str, rel: str) -> None:
    p = _md_for_room(workspace, room)
    if not p:
        return
    text = p.read_text(encoding="utf-8")
    line = embed_line(rel)
    if line not in text:
        return
    text = text.replace("\n\n" + line, "").replace(line + "\n", "").replace(line, "")
    p.write_text(text, encoding="utf-8")


async def import_raw(pool, workspace: Path) -> None:
    """書き終わった生の録音を Ogg Opus にして録音フォルダへ移し、通話と md に結びつける。
    ⚠生の wav は変換に成功したら消す (VM のディスクを食わせない)。失敗したら残して記録する"""
    if not RAW_DIR.is_dir():
        return
    groups: dict[str, dict[str, Path]] = {}
    for p in RAW_DIR.glob("*.wav"):
        stem = p.stem
        kind = "mix"
        for k in ("rx", "tx"):
            if stem.endswith("_" + k):
                stem, kind = stem[: -len(k) - 1], k
        groups.setdefault(stem, {})[kind] = p
    now = time.time()
    for base in sorted(groups):
        files = groups[base]
        if base in _failed:
            continue
        m = _RAW_RE.match(base)
        if not m:
            continue
        try:
            newest = max(f.stat().st_mtime for f in files.values())
        except FileNotFoundError:
            continue
        if now - newest < SETTLE_SEC:
            continue
        t = datetime.strptime(m[1], "%Y%m%d_%H%M%S").replace(tzinfo=JST)
        number = m[3]
        # 録音の始まり (関門の直後) から AI/本人がルームに入る (calls 行ができる) までには、
        # 録音告知・呼び出し (スタンバイ 8 秒・人が出る相手 20 秒・自分で出る 42 秒) が挟まる
        call = await pool.fetchrow(
            """SELECT id, room_name, ended_at FROM calls
               WHERE caller_number = $1 AND recording_path IS NULL
                 AND started_at BETWEEN $2::timestamptz - interval '30 seconds' AND $2::timestamptz + interval '3 minutes'
               ORDER BY started_at LIMIT 1""",
            number,
            t,
        )
        # 通話中ならまだ (⚠ただし agent が落ちて ended_at が付かないまま 1 時間たったものは進める)
        if call and call["ended_at"] is None and now - newest < 3600:
            continue
        name = f"{t:%Y-%m-%d_%H%M%S}_{number or 'unknown'}"
        rel = f"{REC_DIR_NAME}/{t:%Y-%m}/{name}.ogg"
        out = workspace / rel
        n = 1
        while out.exists():  # 同じ秒・同じ番号 (ほぼ無い) は連番
            n += 1
            rel = f"{REC_DIR_NAME}/{t:%Y-%m}/{name}-{n}.ogg"
            out = workspace / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            _encode(files, out)
        except Exception as e:
            _failed.add(base)
            log.error("recording: 変換できない %s — %s (生の wav は残す)", base, e)
            continue
        if call:
            await pool.execute("UPDATE calls SET recording_path=$2 WHERE id=$1", call["id"], rel)
            try:
                link_in_md(workspace, call["room_name"], rel)
            except Exception:
                log.exception("recording: md に埋め込めない %s", rel)
        for f in files.values():
            f.unlink(missing_ok=True)
        log.info("recording: %s (%s, %.0fKB)", rel, "通話あり" if call else "通話なし",
                 out.stat().st_size / 1024)


def _rec_date(p: Path) -> date | None:
    m = _OUT_RE.match(p.name)
    if not m:
        return None
    try:
        return date.fromisoformat(m[1])
    except ValueError:
        return None


async def delete_range(pool, workspace: Path, frm: date | None, to: date | None) -> int:
    """録音の日付 (JST) が frm〜to (両端含む) のものを消す。None は端なし。消した数を返す"""
    root = rec_root(workspace)
    if not root.is_dir():
        return 0
    gone = 0
    for p in sorted(root.glob("*/*.ogg")):
        d = _rec_date(p)
        if d is None or (frm and d < frm) or (to and d > to):
            continue
        rel = p.relative_to(workspace).as_posix()
        p.unlink(missing_ok=True)
        gone += 1
        row = await pool.fetchrow(
            "UPDATE calls SET recording_path=NULL WHERE recording_path=$1 RETURNING room_name", rel
        )
        if row:
            try:
                unlink_in_md(workspace, row["room_name"], rel)
            except Exception:
                log.exception("recording: md の埋め込みを外せない %s", rel)
    for d in root.iterdir():  # 空になった月のフォルダ
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    return gone


async def apply_retention(pool, workspace: Path) -> None:
    """設定の保存日数 (recording_retention_days、空/0 = 消さない) を過ぎた録音を消す"""
    v = await pool.fetchval("SELECT value FROM settings WHERE key='recording_retention_days'")
    try:
        days = int(v or 0)
    except ValueError:
        days = 0
    if days <= 0:
        return
    cutoff = datetime.now(JST).date() - timedelta(days=days)
    n = await delete_range(pool, workspace, None, cutoff - timedelta(days=1))
    if n:
        log.info("recording: 保存期間 %d 日を過ぎた録音を %d 件消した", days, n)


async def forget_missing(pool, workspace: Path) -> None:
    """Obsidian や Drive で直接消された録音を、DB からも外す (管制室に死んだ再生バーを出さない)"""
    rows = await pool.fetch("SELECT id, room_name, recording_path FROM calls WHERE recording_path IS NOT NULL")
    for r in rows:
        if not (workspace / r["recording_path"]).exists():
            await pool.execute("UPDATE calls SET recording_path=NULL WHERE id=$1", r["id"])
            try:
                unlink_in_md(workspace, r["room_name"], r["recording_path"])
            except Exception:
                pass


async def run_delete_job(pool, workspace: Path, query: str) -> str:
    """管制室の「期間を指定して消す」。query は {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"} (どちらも任意)"""
    q = json.loads(query or "{}")
    frm = date.fromisoformat(q["from"]) if q.get("from") else None
    to = date.fromisoformat(q["to"]) if q.get("to") else None
    n = await delete_range(pool, workspace, frm, to)
    log.info("recording: 期間指定で %d 件消した (%s〜%s)", n, frm, to)
    return json.dumps({"deleted": n})
