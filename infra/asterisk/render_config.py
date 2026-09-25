#!/usr/bin/env python3
"""pjsip.conf.template に .env の値を埋めて pjsip.conf を生成する。

Render-Config.ps1 と同じことをする Python 版。VM (debian) には PowerShell が無いので
そちらではこちらを使う。⚠両方を直すこと — 片方だけ直すと環境で結果が変わる。

  python3 infra/asterisk/render_config.py                 # 公開IPを自動取得
  python3 infra/asterisk/render_config.py --public-ip X.X.X.X

生成された pjsip.conf は SIP 認証情報を含むので .gitignore 対象。
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent

REQUIRED = [
    "BRASTEL_SIP_USER",
    "BRASTEL_SIP_PASSWORD",
    "BRASTEL_SIP_SERVER",
    "TWILIO_SIP_USER",
    "TWILIO_SIP_PASSWORD",
    "TWILIO_TERMINATION_URI",
    # ソフトフォン内線 (2026-09-17)。Tailscale 経由でのみ到達する
    "PHONE_1001_PASSWORD",
    "PHONE_1002_PASSWORD",
    "PHONE_1003_PASSWORD",
]

ENV_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
PLACEHOLDER = re.compile(r"\$\{[A-Z0-9_]+\}")


def load_env(path: Path) -> dict[str, str]:
    if not path.exists():
        sys.exit(f".env が見つかりません: {path}")
    vars: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = ENV_LINE.match(line)
        if m:
            vars[m.group(1)] = m.group(2).strip()
    return vars


def fetch_public_ip() -> str:
    with urllib.request.urlopen("https://api.ipify.org", timeout=5) as r:
        ip = r.read().decode().strip()
    if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip):
        sys.exit(f"公開IPの取得に失敗しました: {ip}")
    return ip


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--public-ip",
        help="SDP/Contact に書く公開IP。省略すると api.ipify.org から取る "
        "(⚠VMでは静的IPを明示したほうが確実)",
    )
    args = ap.parse_args()

    vars = load_env(REPO_ROOT / ".env")
    missing = [k for k in REQUIRED if not vars.get(k)]
    if missing:
        sys.exit(".env に次のキーがありません: " + ", ".join(missing))

    vars["PUBLIC_IP"] = args.public_ip or fetch_public_ip()
    print(f"公開IP: {vars['PUBLIC_IP']}")

    tpl = (HERE / "pjsip.conf.template").read_text(encoding="utf-8")
    for k, v in vars.items():
        tpl = tpl.replace("${" + k + "}", v)
    left = PLACEHOLDER.search(tpl)
    if left:
        sys.exit(f"未解決のプレースホルダが残っています: {left.group(0)}")

    out = HERE / "pjsip.conf"
    out.write_text(tpl, encoding="utf-8")
    print(f"{out} を生成しました (git 管理外)")


if __name__ == "__main__":
    main()
