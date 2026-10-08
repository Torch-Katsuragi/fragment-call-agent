# -*- coding: utf-8 -*-
"""テナント (管制室) の台帳を読む (2026-10-04)。

VM 1 台に複数の管制室を同居させる。agent は全テナントで共有し (待機プロセス 1 つ約 380MB を
テナントごとに持つと 4GB に入らない)、通話ごとに部屋名の頭からテナントを決めて、
そのテナントの DB・作業フォルダ・hookd・名乗りで動く。台帳は infra/tenants/tenants.json。
"""

import json
import os
from pathlib import Path

TENANTS_FILE = Path(os.environ.get("TENANTS_FILE", "/tenant-registry/tenants.json"))
DEFAULT_ID = "main"


def load() -> list[dict]:
    try:
        return json.loads(TENANTS_FILE.read_text(encoding="utf-8")).get("tenants", [])
    except (OSError, ValueError):
        return []


def by_id(tid: str) -> dict | None:
    return next((t for t in load() if t.get("id") == tid), None)


def for_room(room: str) -> dict | None:
    """部屋名 <room_prefix>_<番号>_<乱数> からテナントを引く。台帳が無い・当たらないときは None
    (= 環境変数のまま動く。台帳を入れる前と同じ)"""
    for t in load():
        p = t.get("room_prefix") or ""
        if p and room.startswith(p + "_"):
            return t
    return None


def env_for(t: dict) -> dict[str, str]:
    """そのテナントで動くときの環境変数。⚠DB の接続先はホストとユーザーを今の DATABASE_URL から引き継ぐ"""
    base = os.environ.get("DATABASE_URL", "")
    env: dict[str, str] = {}
    if base and t.get("database"):
        env["DATABASE_URL"] = base.rsplit("/", 1)[0] + "/" + t["database"]
    if t.get("workspace_container"):
        env["FRAGMENT_WORKSPACE"] = t["workspace_container"]
    if t.get("hookd"):
        env["HOOKD_URL"] = t["hookd"]
    key = os.environ.get(f"TENANT_{t['id'].upper()}_GOOGLE_API_KEY")
    if key:
        env["GOOGLE_API_KEY"] = key
    # AI の請求先 (2026-10-08、vertex.py)。<ID>_VERTEX_PROJECT (例 KUMIAI_VERTEX_PROJECT) があれば
    # そのテナントの GCP プロジェクトで Gemini を呼ぶ。無ければ main と同じ (VERTEX_PROJECT のまま)
    proj = os.environ.get(f"{t['id'].upper()}_VERTEX_PROJECT")
    if proj and t["id"] != DEFAULT_ID:
        env["VERTEX_PROJECT"] = proj
    owner = os.environ.get(f"TENANT_{t['id'].upper()}_OWNER_NAME")
    if owner is not None:
        env["OWNER_NAME"] = owner
    elif t["id"] != DEFAULT_ID:
        # ⚠他のテナントにユーザーの氏名 (.env の OWNER_NAME) を引き継がない。名乗りは各管制室の設定で決まる
        env["OWNER_NAME"] = ""
    env["TENANT_ID"] = t["id"]
    return env
