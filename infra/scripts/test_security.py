#!/usr/bin/env python3
"""警備の静的判定 (security.classify / normalize / came_via_intl) のテスト。

なぜ要るか (2026-08-01):
  security.py の classify は「単体テストしやすいように純関数にしてある」と書いてあるのに、
  実際のテストが1本も無かった。ここは**誤検知するとまともな着信を落とす**場所なので、
  線引きを固定しておく。DBもコンテナも要らない (標準ライブラリだけで動く)。

実行 (どこでも):
  python3 infra/scripts/test_security.py
"""

import os
import sys
from pathlib import Path

# 自分のDIDを固定してから読み込む。⚠環境変数は読み込み時に評価されるので順序が要る
os.environ.setdefault("SECURITY_LOCAL_NUMBERS", "05012345678")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "agent"))

import security  # noqa: E402

fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def test_normalize() -> None:
    print("[normalize] 表記揺れを畳む")
    for raw, want in [
        ("+81-50-1234-5678", "05012345678"),
        ("05012345678", "05012345678"),
        ("090 1234 5678", "09012345678"),
        ("", ""),
        (None, ""),
        ("anonymous", ""),
    ]:
        got = security.normalize(raw)
        check(f"{raw!r} → {want!r}", got == want, got)
    # ⚠この畳み込みが「国際経由で届いた」事実を消す。だから came_via_intl が別に要る
    check(
        "国際表記と国内表記が同じ文字列に畳まれる",
        security.normalize("+819012345678") == security.normalize("09012345678"),
    )


def test_came_via_intl() -> None:
    print("[came_via_intl] 畳む前の経路を拾う")
    for raw, want in [
        ("+819012345678", True),
        ("008190123456789", True),
        ("09012345678", False),
        ("+14155550123", False),  # 本物の国際発信は日本の国番号ではない
        ("anonymous", False),
        (None, False),
    ]:
        check(f"{raw!r} → {want}", security.came_via_intl(raw) is want)


def test_relay() -> None:
    print("[classify] 国際中継の踏み台狙い — 事故の本体")
    # ⚠7/26の実攻撃そのもの。**kind は premium_relay にならない** —
    #   国番号以降が 441904911180 (英国 01904 York帯) で PREMIUM_LABELS に当たらないため。
    #   拒否は「着信なのに宛先が国際番号」の一点で成立していて、ラベルは付録にすぎない
    v = security.classify("1001", "0009441904911180")
    check("7/26の実攻撃を拒否する", v.reject, v.detail)
    check("ただしプレミアム帯としては認識しない", v.kind == "international_relay", v.kind)
    v = security.classify("09012345678", "01012345678901")
    check("010プレフィックスも拒否", v.reject, v.detail)
    # 001(事業者選択) + 1(米国) + 900(プレミアム)。**4桁プレフィックスとしても読める**ので、
    # 候補を両方出していないとラベルが付かない (貪欲マッチ時代はここを取りこぼしていた)
    v = security.classify("1001", "00119001234567")
    check("表に載っている帯はラベルが付く", v.kind == "premium_relay", v.detail)
    check(
        "読み方が2通りあるときは両方を候補に残す",
        len(security._intl_candidates("00119001234567")) == 2,
        str(security._intl_candidates("00119001234567")),
    )


def test_scanner() -> None:
    print("[classify] スキャナの特徴")
    v = security.classify("1001", "05012345678")
    check("内線風の短い番号は拒否", v.reject and v.kind == "extension_scan", v.detail)
    v = security.classify("sipvicious", "05012345678")
    check("数字ですらない発信者は拒否", v.reject and v.kind == "malformed_caller", v.detail)


def test_normal_calls_pass() -> None:
    """⚠**ここが一番大事**。誤検知でまともな着信を落とすのが最悪の失敗なので、
    正常系を明示的に固定する。"""
    print("[classify] まともな着信は通す")
    for caller in ("09012345678", "0736221111", "+819012345678", "0120117117"):
        v = security.classify(caller, "05012345678")
        check(f"{caller} は通る", not v.reject, v.detail)
    v = security.classify("anonymous", "05012345678")
    check("非通知は通る (プロンプト側に応対がある)", not v.reject, v.detail)
    # 宛先が自分のDIDでなくても、国際番号でなければ既定では通す (STRICT_CALLEE=0)
    v = security.classify("09012345678", "12345")
    check("見慣れない宛先だけでは拒否しない", not v.reject, v.detail)


def main() -> int:
    for t in (
        test_normalize,
        test_came_via_intl,
        test_relay,
        test_scanner,
        test_normal_calls_pass,
    ):
        t()
        print()
    if fails:
        print(f"{len(fails)}件 FAIL: {', '.join(fails)}")
        return 1
    print("全PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
