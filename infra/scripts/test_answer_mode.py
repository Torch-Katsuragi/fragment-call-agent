#!/usr/bin/env python3
"""応答モード (不在 / スタンバイ / 自分で出る) の自動テスト。

なぜ別ファイルか (2026-08-01):
  run_scenarios.py は相手役AIを回すので**1本あたり分単位・Geminiのトークン代がかかる**。
  ここで測りたいのは「2秒だけ鳴らして、出なければAIに渡す」という**時間とフラグの話**で、
  LLMは1回も要らない。秒単位で決まる挙動はLLMを挟むと再現しないので、分けて速く回す。

実行 (VM上、リポジトリ直下から):
  python3 infra/scripts/test_answer_mode.py

⚠settings.answer_mode を書き換える。終了時に必ず元へ戻す (異常終了時も finally で戻す)。
⚠hookd が動いていること。ダイヤルプラン側 (Asterisk) は別途 `dialplan show` で見る。
"""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

HOOKD = "http://localhost:8790"
NUMBER = "0900000777"

# 端末を鳴らす秒数。⚠hookd の STANDBY_RING_SEC / MANUAL_RING_SEC と揃えること。
# 2026-09-18: スタンバイ秒数は固定 2 から .env の STANDBY_RING_SEC (ユーザーの判断で 8) に
#   なったので、ここも同じ源から読む。テストに 2 を焼いたままだと設定を変えた瞬間に
#   「約2秒だけ鳴らす」が嘘の FAIL を出す


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


# ⚠run_scenarios.py の PSQL と揃えてある (コンテナ名・ユーザー名)
PSQL = ["sudo", "docker", "exec", "-i", "infra-postgres-1",
        "psql", "-U", "callagent", "-d", "callagent", "-tA", "-c"]


def psql(sql: str) -> str:
    """DB直叩き。⚠管制室のAPIは認証が要るので、テストはsettingsを直接動かす"""
    out = subprocess.run(PSQL + [sql], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"psql failed: {out.stderr.strip()}")
    return out.stdout.strip()


def set_mode(mode: str) -> None:
    psql(
        "INSERT INTO settings (key, value) VALUES ('answer_mode', '%s') "
        "ON CONFLICT (key) DO UPDATE SET value = '%s', updated_at = now()" % (mode, mode)
    )


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def ring_state() -> dict | None:
    return json.loads(get("/ringing_state")).get("ringing")


def start_ring() -> dict | None:
    get(f"/ringing_start?number={NUMBER}")
    return ring_state()


def case_away() -> None:
    print("[不在] ノータイム応答 — 端末は鳴らさない")
    set_mode("away")
    st = start_ring()
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
    check(f"約{STANDBY_SEC:g}秒だけ鳴らす", want - 500 <= ms <= want + 100, f"ring_ms={ms}")

    t0 = time.time()
    r = get(f"/pickup_wait?number={NUMBER}&timeout={STANDBY_SEC}", timeout=10)
    dt = time.time() - t0
    check("受話されなければ no", r == "no", r)
    # ⚠ここが伸びると相手を無音で待たせる。2.5秒を超えるとワンギリも抜けはじめる
    #   (8 にした 2026-09-18 以降は抜ける前提。折り返し前に /screen の判定を見る運用)
    check(f"{STANDBY_SEC:g}秒で返る", STANDBY_SEC - 0.3 <= dt <= STANDBY_SEC + 0.7, f"{dt:.2f}s")
    after = ring_state()
    check(
        "時間切れ後は端末を鳴らし止める",
        (after or {}).get("ring_ms", 1) == 0,
        str(after),
    )


def case_standby_pickup() -> None:
    print("[スタンバイ] 猶予内に本人が出る → その場で受話")
    set_mode("standby")
    start_ring()

    # ⚠ロングポーリングの要点は「押した瞬間に返る」こと。
    #   2秒間隔のポーリングだった頃は、押しても最大2秒待たされて猶予を食い潰していた
    threading.Timer(0.4, lambda: get(f"/pickup_request?number={NUMBER}")).start()
    t0 = time.time()
    r = get(f"/pickup_wait?number={NUMBER}&timeout={STANDBY_SEC}", timeout=10)
    dt = time.time() - t0
    check("受話されたら yes", r == "yes", r)
    check("押した瞬間に返る", dt < 1.0, f"{dt:.2f}s")


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
    total = psql("SELECT count(*) FROM device_push_tokens").strip()
    if total in ("", "0"):
        print("  (端末の登録が無いので省略)")
        return
    before = psql("SELECT string_agg(token, ',') FROM device_push_tokens WHERE NOT paused").strip()
    try:
        psql("UPDATE device_push_tokens SET paused = true")
        set_mode("standby")
        start_ring()
        t0 = time.time()
        r = get(f"/pickup_wait?number={NUMBER}&timeout={STANDBY_SEC}", timeout=10)
        dt = time.time() - t0
        check("受話なし (no)", r == "no", r)
        check("待たずに返る (1秒未満)", dt < 1.0, f"{dt:.2f}s")
    finally:
        # 元に戻す。⚠一時停止していた端末まで起こさない (元々 NOT paused だったものだけ戻す)
        for tok in [t for t in before.split(",") if t]:
            psql("UPDATE device_push_tokens SET paused = false WHERE token = '%s'" % tok)


def case_unknown_number() -> None:
    print("[異常系] 鳴っていない番号")
    r = get("/pickup_wait?number=0900000000&timeout=1", timeout=5)
    # ⚠ここで待たせるとダイヤルプランが無駄に止まる。知らない番号は即 no
    check("即座に no", r == "no", r)


def main() -> int:
    before = psql("SELECT value FROM settings WHERE key = 'answer_mode'") or ""
    # ⚠テスト中だけサーバー側の一時停止を外す (2026-09-24)。全端末が一時停止だとスタンバイが
    #   待たずに返る (それが仕様) ので、「8秒で返る」等の前提が崩れて FAIL する。
    #   端末側の Prefs.paused には触らないので、テスト中も一時停止中の端末は鳴らない
    paused_before = [
        t for t in psql("SELECT string_agg(token, ',') FROM device_push_tokens WHERE paused").strip().split(",") if t
    ]
    if paused_before:
        psql("UPDATE device_push_tokens SET paused = false")
        print(f"(一時停止中の端末 {len(paused_before)} 台をテスト中だけサーバー側で解除)")
        print()
    try:
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
        if before:
            set_mode(before)
        else:
            psql("DELETE FROM settings WHERE key = 'answer_mode'")
        print(f"応答モードを元に戻した: {before or '(未設定)'}")
        for tok in paused_before:
            psql("UPDATE device_push_tokens SET paused = true WHERE token = '%s'" % tok)
        if paused_before:
            print(f"一時停止を元に戻した: {len(paused_before)} 台")

    if fails:
        print(f"\n{len(fails)}件 FAIL: {', '.join(fails)}")
        return 1
    print("\n全PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
