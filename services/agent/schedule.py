# -*- coding: utf-8 -*-
"""応答モードの時間割と、人ごとの受付時間 (2026-09-29)。

回線全体の時間割 (settings.schedule):
  曜日ごとの時間帯に応答モード (away / standby / manual) を割り当て、時間帯の外は default。
  例: 平日 8:00〜17:00 はスタンバイ、それ以外と土日祝は不在 (ノータイムで AI)。
手で切り替えたとき (settings.answer_mode + answer_mode_until):
  時間割が有効なら**次の切り替わりまで**手動が勝ち、そこで時間割に戻る (エアコンのタイマーと同じ)。
  時間割が無効なら従来どおり手動の値がそのまま続く。ただしスタンバイだけは切り忘れ対策で
  STANDBY_HOURS 後に不在へ戻る (以前はアプリの端末側タイマーが持っていた。サーバーに移した)。
人ごとの受付時間 (members.hours):
  その人の端末を鳴らしてよい時間。外れている人の端末には着信と AI の取り次ぎを出さない。
  null = いつでも。⚠保留中に人が名指しで呼ぶのは対象外 (呼ぶ側の判断)。

⚠判定はここ (hookd) だけで行う。管制室とアプリは /answer_mode_info を見る。
  祝日の表を TS と Python の両方に持つと食い違うので、二重に書かないこと。

形 (どちらも JSON):
  schedule = {"enabled": bool, "default": "away",
              "days": {"mon": [{"start": "08:00", "end": "17:00", "mode": "standby"}], ..., "hol": []},
              "closed": ["2026-12-29", ...]}          # 休業日 (祝日と同じ扱い)
  hours    = {"days": {"mon": [{"start": "08:00", "end": "17:00"}], ...}}  # mode は無い
  hol = 祝日と休業日。その日は曜日の欄ではなく hol の欄を使う。
  end が start 以下の時間帯は日をまたぐ (22:00〜06:00 = その日の 22:00 から翌 06:00)。
"""

import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

import members

log = logging.getLogger("schedule")

JST = timezone(timedelta(hours=9))  # ⚠日本に夏時間は無い。コンテナの tzdata に頼らない
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MODES = ("away", "standby", "manual")
STANDBY_HOURS = float(os.environ.get("STANDBY_HOURS") or "8")

DDL = """
ALTER TABLE members ADD COLUMN IF NOT EXISTS hours jsonb;
"""

try:
    import jpholiday
except Exception:  # 入っていなければ祝日なし扱い (曜日どおり)
    jpholiday = None
    log.warning("jpholiday が無い — 祝日は曜日どおりに扱う")


def _is_hol(d: date, closed: set[str]) -> bool:
    if d.isoformat() in closed:
        return True
    return bool(jpholiday and jpholiday.is_holiday(d))


def _minutes(hhmm: str) -> int | None:
    try:
        h, m = hhmm.split(":")
        v = int(h) * 60 + int(m)
        return v if 0 <= v <= 24 * 60 else None
    except (ValueError, AttributeError):
        return None


def _blocks(table: dict, d: date, closed: set[str]) -> list[dict]:
    key = "hol" if _is_hol(d, closed) else DAYS[d.weekday()]
    return [b for b in (table.get("days") or {}).get(key) or [] if isinstance(b, dict)]


def _covering(table: dict, t: datetime, closed: set[str]) -> dict | None:
    """t を含む時間帯。その日の欄と、前日の欄から日をまたいで来るものを見る。後に書いた方が勝つ"""
    now_m = t.hour * 60 + t.minute
    hit = None
    for b in _blocks(table, t.date(), closed):
        s, e = _minutes(b.get("start", "")), _minutes(b.get("end", ""))
        if s is None or e is None:
            continue
        if (s < e and s <= now_m < e) or (e <= s and now_m >= s):
            hit = b
    if hit:
        return hit
    for b in _blocks(table, t.date() - timedelta(days=1), closed):
        s, e = _minutes(b.get("start", "")), _minutes(b.get("end", ""))
        if s is not None and e is not None and e <= s and now_m < e:
            hit = b
    return hit


def _parse(raw: str | None) -> dict:
    try:
        v = json.loads(raw or "")
        return v if isinstance(v, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def scheduled_mode(sched: dict, t: datetime) -> str:
    closed = set(sched.get("closed") or [])
    b = _covering(sched, t, closed)
    m = (b or {}).get("mode") if b else sched.get("default")
    return m if m in MODES else "away"


def in_hours(hours: dict | None, t: datetime) -> bool:
    if not hours or not isinstance(hours, dict) or not hours.get("days"):
        return True  # 未設定 = いつでも
    return _covering(hours, t, set()) is not None


def next_change(sched: dict, t: datetime) -> tuple[datetime, str] | None:
    """時間割で次にモードが変わる時刻と、その後のモード。8 日先まで分刻みの境目だけを見る"""
    closed = set(sched.get("closed") or [])
    cur = scheduled_mode(sched, t)
    # 候補 = 各日の 0:00 と時間帯の始まり・終わり
    cands: set[datetime] = set()
    base = t.replace(hour=0, minute=0, second=0, microsecond=0)
    for i in range(0, 9):
        day = base + timedelta(days=i)
        cands.add(day)
        for b in _blocks(sched, day.date(), closed):
            for k in ("start", "end"):
                m = _minutes(b.get(k, ""))
                if m is not None:
                    cands.add(day + timedelta(minutes=m))
    for c in sorted(x for x in cands if x > t):
        m = scheduled_mode(sched, c)
        if m != cur:
            return c, m
    return None


async def _settings(pool) -> dict[str, str]:
    rows = await pool.fetch(
        "SELECT key, value FROM settings WHERE key IN "
        "('answer_mode', 'assistant_enabled', 'answer_mode_until', 'schedule')"
    )
    return {r["key"]: r["value"] for r in rows}


def _manual_value(s: dict[str, str]) -> str:
    m = s.get("answer_mode")
    if m in MODES:
        return m
    return "manual" if s.get("assistant_enabled") == "false" else "away"


async def info(pool, now: float | None = None) -> dict:
    """いまの応答モードと、その出どころ。
    source: schedule = 時間割どおり / manual = 手で切り替えた (until まで) / fixed = 時間割なしの手動
    next = 次に変わる時刻とモード (わからなければ None)"""
    now = now or time.time()
    t = datetime.fromtimestamp(now, JST)
    s = await _settings(pool)
    sched = _parse(s.get("schedule"))
    enabled = bool(sched.get("enabled"))
    try:
        until = float(s.get("answer_mode_until") or 0)
    except ValueError:
        until = 0.0
    manual = _manual_value(s)
    nxt = next_change(sched, t) if enabled else None

    if until and now < until:
        mode, source = manual, "manual"
        back = scheduled_mode(sched, datetime.fromtimestamp(until, JST)) if enabled else "away"
        next_at, next_mode = until, back
    elif enabled:
        mode, source = scheduled_mode(sched, t), "schedule"
        next_at, next_mode = (nxt[0].timestamp(), nxt[1]) if nxt else (None, None)
    elif until:
        # 時間割なしのスタンバイが期限切れ → 不在
        mode, source, next_at, next_mode = "away", "fixed", None, None
    else:
        mode, source, next_at, next_mode = manual, "fixed", None, None
    return {
        "mode": mode,
        "source": source,
        "schedule_enabled": enabled,
        "next_at": next_at,
        "next_mode": next_mode,
    }


async def effective_mode(pool) -> str:
    return (await info(pool))["mode"]


# ---- 相手ごとの応答 (2026-09-30) ----
# 電話帳md (連絡先/<番号>/<番号>.md) の `応答:` で、その相手だけ回線の応答モードを上書きする。
#   人が出る … 不在・スタンバイでも PRIORITY_RING_SEC だけ端末を鳴らし、出なければ AI
#              (「自分で出る」のときは元々鳴らし続けるのでそのまま)
#   AIが出る … いつもノータイムで AI (端末は鳴らさない)
# ⚠書くのは人 (アプリ・管制室の電話帳)。機械は書かない。発信者番号は偽装され得るが、
#   ここは「鳴らすかどうか」だけで情報は出さないので、番号で決めてよい
#   (緊急呼び出しの許可を番号で推測しないのとは別の話 — あちらは本人を呼び出す権限)
PRIORITY_RING_SEC = float(os.environ.get("PRIORITY_RING_SEC") or "20")
_ANSWER_VALUES = {"人が出る": "human", "AIが出る": "ai"}


def contact_answer(number: str) -> str | None:
    """その番号の `応答:`。human / ai / None (回線の設定に従う)"""
    if not number or not number.isdigit():
        return None
    ws = os.environ.get("FRAGMENT_WORKSPACE", "")
    if not ws:
        return None
    from pathlib import Path

    book = Path(ws) / "連絡先"
    for p in (book / number / f"{number}.md", book / f"{number}.md"):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        m = re.search(r"^応答:\s*(\S+)\s*$", text, re.M)
        return _ANSWER_VALUES.get(m.group(1)) if m else None
    return None


async def call_plan(pool, number: str = "") -> tuple[str, float | None]:
    """この着信の応答モードと、鳴らす秒数の上書き (None = モードの既定)。
    返り値のモードは away / standby / manual のどれか。人が出る相手は standby + PRIORITY_RING_SEC"""
    mode = await effective_mode(pool)
    who = contact_answer(number)
    if who == "ai":
        return "away", None
    if who == "human" and mode != "manual":
        return "standby", PRIORITY_RING_SEC
    return mode, None


async def set_manual(pool, mode: str) -> dict:
    """手で切り替える。時間割が有効なら次の切り替わりまで、無効ならスタンバイだけ STANDBY_HOURS。
    ⚠いま時間割どおりのモードを選んだときは上書きを作らない (時間割に従っている状態のまま)"""
    now = time.time()
    t = datetime.fromtimestamp(now, JST)
    s = await _settings(pool)
    sched = _parse(s.get("schedule"))
    until = 0.0
    if sched.get("enabled"):
        if mode != scheduled_mode(sched, t):
            nxt = next_change(sched, t)
            until = nxt[0].timestamp() if nxt else now + 24 * 3600
    elif mode == "standby":
        until = now + STANDBY_HOURS * 3600
    for k, v in (
        ("answer_mode", mode),
        ("assistant_enabled", "false" if mode == "manual" else "true"),
        ("answer_mode_until", str(int(until)) if until else ""),
    ):
        await pool.execute(
            """INSERT INTO settings (key, value) VALUES ($1, $2)
               ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = now()""",
            k,
            v,
        )
    return await info(pool, now)


def _member_of(device_expr: str) -> str:
    """端末 → いまその端末でログインしているメンバー。⚠devices.member_id ではなくログイン
    (device_sessions) を正にする — ログインし直し・締め出しで変わるのはこちら"""
    return f"""(SELECT s.member_id FROM device_sessions s
        WHERE s.device_id = {device_expr} AND s.revoked_at IS NULL ORDER BY s.created_at DESC LIMIT 1)"""


async def ring_devices(pool) -> tuple[int, list[str], list[str]]:
    """着信・取り次ぎで鳴らしてよい端末。
    返り値: (登録端末の総数, 鳴らす端末 id, 受付時間外の端末 id)。
    ⚠一時停止中の端末はどちらにも入らない (総数には入る)。
    ⚠数えるのは有効なログインで起こす宛先がある端末だけ (2026-10-04、端末の情報はログインの行だけに持つ)。
      ログアウトや締め出しで外れる — 以前は前の管制室で試した端末が残り、スタンバイが待ち続けた"""
    t = datetime.now(JST)
    try:
        rows = await pool.fetch(
            f"""SELECT DISTINCT ON (s.device_id) s.device_id, s.paused, m.hours
                FROM {members.ACTIVE_SESSIONS}
                WHERE s.push_token IS NOT NULL
                ORDER BY s.device_id, s.created_at DESC"""
        )
    except Exception:
        log.exception("端末の受付時間が読めない — 全部鳴らす")
        return 0, [], []
    ring: list[str] = []
    quiet: list[str] = []
    for r in rows:
        if r["paused"]:
            continue
        h = r["hours"]
        if isinstance(h, str):
            h = _parse(h)
        (ring if in_hours(h, t) else quiet).append(r["device_id"] or "")
    return len(rows), ring, quiet


async def device_in_hours(pool, device_id: str) -> bool:
    if not device_id:
        return True
    try:
        h = await pool.fetchval(
            f"SELECT hours FROM members WHERE id = {_member_of('$1')}",
            device_id,
        )
    except Exception:
        return True
    if isinstance(h, str):
        h = _parse(h)
    return in_hours(h, datetime.now(JST))
