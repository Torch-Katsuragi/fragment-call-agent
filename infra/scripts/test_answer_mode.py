#!/usr/bin/env python3
"""応答モード (不在 / スタンバイ / 自分で出る) の自動テスト。

なぜ別ファイルか (2026-08-01):
  run_scenarios.py は相手役AIを回すので**1本あたり分単位・Geminiのトークン代がかかる**。
  ここで測りたいのは「数秒だけ鳴らして、出なければAIに渡す」という**時間とフラグの話**で、
  LLMは1回も要らない。秒単位で決まる挙動はLLMを挟むと再現しないので、分けて速く回す。

実行 (VM上、リポジトリ直下から):
  python3 infra/scripts/test_answer_mode.py

⚠settings (answer_mode / assistant_enabled / answer_mode_until / schedule) と
  device_sessions.paused / members.hours をテスト中だけ一時変更し、終了時に必ず元へ戻す
  (異常終了時も finally で戻す)。これを行わないと夜間・休日の時間割や受付時間外判定で
  常に away / no_listener に倒れてしまう。
⚠hookd が動いていること。ダイヤルプラン側 (Asterisk) は別途 `dialplan show` で見る。
"""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HOOKD = "http://localhost:8790"
NUMBER = "0900000777"


def _env_from_dotenv(key: str, default: str) -> str:
    """環境変数 → リポジトリ直下の .env → 既定、の順。VM 上で素の python3 で走らせるので
    compose のように .env を自動では読んでくれない"""
    if os.environ.get(key):
        return os.environ[key]
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        with open(os.path.join(root, ".env"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'") or default
    except OSError:
        pass
    return default


STANDBY_SEC = float(_env_from_dotenv("STANDBY_RING_SEC", "2"))
MANUAL_SEC = float(_env_from_dotenv("MANUAL_RING_SEC", "42"))

fails: list[str] = []


def get(path: str, timeout: float = 10.0) -> str:
    with urllib.request.urlopen(HOOKD + path, timeout=timeout) as r:
        return r.read().decode().strip()


def get_status(path: str, timeout: float = 10.0) -> int:
    try:
        with urllib.request.urlopen(HOOKD + path, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


# ⚠run_scenarios.py の PSQL と揃えてある (コンテナ名・ユーザー名)
PSQL = ["sudo", "docker", "exec", "-i", "infra-postgres-1",
        "psql", "-U", "callagent", "-d", "callagent", "-tA", "-c"]


def psql(sql: str) -> str:
    """DB直叩き。⚠管制室のAPIは認証が要るので、テスト前後の退避・復元に使う"""
    out = subprocess.run(PSQL + [sql], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"psql failed: {out.stderr.strip()}")
    return out.stdout.strip()


def _sql_lit(val: str) -> str:
    return "'" + val.replace("'", "''") + "'"


def set_mode(mode: str) -> None:
    """hookd の公式エンドポイント (/answer_mode_set) 経由で切り替える (schedule.set_manual も通す)"""
    res = json.loads(get(f"/answer_mode_set?mode={mode}"))
    if not res.get("ok"):
        raise RuntimeError(f"answer_mode_set failed: {res}")


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def ring_state() -> dict | None:
    return json.loads(get("/ringing_state")).get("ringing")


def start_ring(*, finish_announce: bool = True) -> dict | None:
    """着信開始 (/ringing_start) し、既定では録音告知完了 (/ringing_phase) まで進める"""
    get(f"/ringing_start?number={NUMBER}")
    if finish_announce:
        get(f"/ringing_phase?number={NUMBER}")
    return ring_state()


def case_away() -> None:
    print("[不在] ノータイム応答 — 端末は鳴らさない")
    set_mode("away")
    st = start_ring(finish_announce=False)
    check("モードが away で登録される", (st or {}).get("mode") == "away", str(st))
    # ⚠ここが 0 でないと、2026-07-30のワンギリ対策 (即応答) が骨抜きになる。
    #   端末が鳴っている間ダイヤルプランが待つ、という作りに転びやすいので固定する
    check("鳴らす時間が0", (st or {}).get("ring_ms", -1) == 0, str(st))
    check("/answer_mode が away", get("/answer_mode") == "away")
    check("/assistant は互換で on", get("/assistant") == "on")


def case_standby_timeout() -> None:
    print(f"[スタンバイ] 誰も出ない → {STANDBY_SEC:g}秒でAIに渡す")
    set_mode("standby")
    st = start_ring()
    check("モードが standby", (st or {}).get("mode") == "standby", str(st))
    ms = (st or {}).get("ring_ms", 0)
    want = STANDBY_SEC * 1000
    check(f"約{STANDBY_SEC:g}秒だけ鳴らす", want - 500 <= ms <= want + 200, f"ring_ms={ms}")

    t0 = time.time()
    r = get(f"/pickup_wait?number={NUMBER}&timeout={STANDBY_SEC}", timeout=12)
    dt = time.time() - t0
    check("受話されなければ no", r == "no", r)
    check(f"{STANDBY_SEC:g}秒で返る", STANDBY_SEC - 0.3 <= dt <= STANDBY_SEC + 0.7, f"{dt:.2f}s")
    after = ring_state()
    check(
        "時間切れ後は端末を鳴らし止める",
        (after or {}).get("ring_ms", 1) == 0,
        str(after),
    )


def case_standby_pickup() -> None:
    print("[スタンバイ] 告知中は早押し拒否 (409) → 告知完了後は押した瞬間に受話")
    set_mode("standby")
    st_ann = start_ring(finish_announce=False)
    check("告知中は phase=announcing", (st_ann or {}).get("phase") == "announcing", str(st_ann))
    check("告知中の受話早押しは 409", get_status(f"/pickup_request?number={NUMBER}") == 409)

    st_ring = json.loads((get(f"/ringing_phase?number={NUMBER}"), get("/ringing_state"))[1]).get("ringing")
    check("告知完了で phase=ringing", (st_ring or {}).get("phase") == "ringing", str(st_ring))

    # ⚠ロングポーリングの要点は「押した瞬間に返る」こと。
    threading.Timer(0.4, lambda: get(f"/pickup_request?number={NUMBER}")).start()
    t0 = time.time()
    r = get(f"/pickup_wait?number={NUMBER}&timeout={STANDBY_SEC}", timeout=12)
    dt = time.time() - t0
    check("受話されたら yes", r == "yes", r)
    check("押した瞬間に返る", dt < 1.0, f"{dt:.2f}s")
    # /picked_up の読み捨てフラグも掃除しておく
    get(f"/picked_up?number={NUMBER}")


def case_manual() -> None:
    print("[自分で出る] 鳴らし続ける — 細切れの待ちで鳴り止まない")
    set_mode("manual")
    st = start_ring()
    check("モードが manual", (st or {}).get("mode") == "manual", str(st))
    ms = (st or {}).get("ring_ms", 0)
    check("40秒以上鳴らす", ms >= 40000, f"ring_ms={ms}")
    check("/assistant は off", get("/assistant") == "off")

    # ⚠ダイヤルプランは4秒の /pickup_wait を繰り返す。1回目の時間切れで
    #   ring_until を潰すと、そこで端末が鳴り止んでしまう (実装時に踏んだ)
    t0 = time.time()
    r = get(f"/pickup_wait?number={NUMBER}&timeout=1", timeout=10)
    check("時間切れは no", r == "no", f"{r} ({time.time() - t0:.2f}s)")
    after = ring_state()
    check(
        "時間切れでも鳴らし続ける",
        (after or {}).get("ring_ms", 0) > 30000,
        str(after),
    )


def case_standby_all_paused() -> None:
    """登録端末が全部一時停止ならスタンバイでも待たずに返す (2026-09-24)"""
    print("[スタンバイ] 端末が全部一時停止 → 待たずにAIへ")
    total = psql("SELECT count(*) FROM device_sessions WHERE push_token IS NOT NULL AND revoked_at IS NULL").strip()
    if total in ("", "0"):
        print("  (端末の登録が無いので省略)")
        return
    before = psql("SELECT string_agg(token_hash, ',') FROM device_sessions WHERE NOT paused AND revoked_at IS NULL").strip()
    try:
        psql("UPDATE device_sessions SET paused = true")
        set_mode("standby")
        start_ring()
        t0 = time.time()
        r = get(f"/pickup_wait?number={NUMBER}&timeout={STANDBY_SEC}", timeout=10)
        dt = time.time() - t0
        check("受話なし (no)", r == "no", r)
        check("待たずに返る (1秒未満)", dt < 1.0, f"{dt:.2f}s")
    finally:
        for tok in [t for t in before.split(",") if t]:
            psql(f"UPDATE device_sessions SET paused = false WHERE token_hash = {_sql_lit(tok)}")


def case_unknown_number() -> None:
    print("[異常系] 鳴っていない番号")
    r = get("/pickup_wait?number=0900000000&timeout=1", timeout=5)
    check("即座に no", r == "no", r)


SETTING_KEYS = ("answer_mode", "assistant_enabled", "answer_mode_until", "schedule")


def main() -> int:
    saved_settings: dict[str, str | None] = {}
    for k in SETTING_KEYS:
        has = psql(f"SELECT count(*) FROM settings WHERE key = {_sql_lit(k)}")
        saved_settings[k] = psql(f"SELECT value FROM settings WHERE key = {_sql_lit(k)}") if has == "1" else None

    paused_before = [
        t for t in psql("SELECT string_agg(token_hash, ',') FROM device_sessions WHERE paused AND revoked_at IS NULL").strip().split(",") if t
    ]
    hours_rows_raw = psql("SELECT COALESCE(json_agg(json_build_object('id', id::text, 'hours', hours))::text, '[]') FROM members WHERE hours IS NOT NULL")
    saved_hours = json.loads(hours_rows_raw or "[]")

    try:
        # 時間割と期限切れオーバーライドを一時退避してテスト中のモード指定を確定的にする
        psql("DELETE FROM settings WHERE key IN ('schedule', 'answer_mode_until')")
        if paused_before:
            psql("UPDATE device_sessions SET paused = false")
            print(f"(一時停止中の端末 {len(paused_before)} 台をテスト中だけサーバー側で解除)")
        if saved_hours:
            psql("UPDATE members SET hours = NULL WHERE hours IS NOT NULL")
            print(f"(受付時間設定のあるメンバー {len(saved_hours)} 名をテスト中だけサーバー側で解除)")
        if paused_before or saved_hours:
            print()

        for case in (
            case_away,
            case_standby_timeout,
            case_standby_pickup,
            case_manual,
            case_standby_all_paused,
            case_unknown_number,
        ):
            case()
            print()
    finally:
        for k, val in saved_settings.items():
            if val is not None:
                psql(
                    f"INSERT INTO settings (key, value) VALUES ({_sql_lit(k)}, {_sql_lit(val)}) "
                    f"ON CONFLICT (key) DO UPDATE SET value = {_sql_lit(val)}, updated_at = now()"
                )
            else:
                psql(f"DELETE FROM settings WHERE key = {_sql_lit(k)}")
        print(f"応答モード・時間割設定を元に戻した: answer_mode={saved_settings.get('answer_mode') or '(未設定)'}")

        for tok in paused_before:
            psql(f"UPDATE device_sessions SET paused = true WHERE token_hash = {_sql_lit(tok)}")
        if paused_before:
            print(f"一時停止を元に戻した: {len(paused_before)} 台")

        for row in saved_hours:
            hours_json = json.dumps(row["hours"], ensure_ascii=False)
            psql(f"UPDATE members SET hours = {_sql_lit(hours_json)}::jsonb WHERE id = {_sql_lit(row['id'])}::uuid")
        if saved_hours:
            print(f"受付時間設定を元に戻した: {len(saved_hours)} 名")

    if fails:
        print(f"\n{len(fails)}件 FAIL: {', '.join(fails)}")
        return 1
    print("\n全PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
