# -*- coding: utf-8 -*-
"""Gemini を Vertex AI 経由で呼ぶための部品 (2026-10-08)。

VERTEX_PROJECT があれば Vertex AI (そのプロジェクトの請求)、無ければ従来どおり GOOGLE_API_KEY (AI Studio)。
なぜ: 同居させた管制室の AI 料金をその組織に払ってもらうのに、キーを預からずに済む。組織が自分の
  GCP プロジェクトで VM のサービスアカウントに「Vertex AI ユーザー」を付ければ、その組織の通話は
  その組織の請求で動く。止めたければ組織側でその権限を外すだけ。
認証は VM のサービスアカウント (メタデータサーバーのトークン)。秘密の文字列はどこにも置かない。
⚠Live のモデルは us-central1 にしか無い (2026-10-08 実測。global・asia-* は not found)。
  東京の VM からでも初音までの時間は AI Studio と変わらなかった (中央値 562ms / 600ms)。
  generateContent と明示キャッシュは global で動く
⚠services/directory-agent/vertex.py は同じもの (別イメージなので複製)。直すときは両方
"""

import json
import os
import time
import urllib.request

_METADATA_TOKEN = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)
_token: tuple[str, float] = ("", 0.0)


def project() -> str:
    return os.environ.get("VERTEX_PROJECT", "").strip()


def location() -> str:
    return os.environ.get("VERTEX_LOCATION", "global")


def live_location() -> str:
    return os.environ.get("VERTEX_LIVE_LOCATION", "us-central1")


# Vertex AI は gemini-flash-latest 等の -latest エイリアスが 404 になるため、
# VERTEX_PROJECT 使用時は対応する最新明示モデルへ自動変換する (2026-11-09 実測)
_VERTEX_LATEST_MAP = {
    "gemini-flash-latest": "gemini-3.8-flash",
    "gemini-flash-lite-latest": "gemini-3.5-flash-lite",
    "gemini-pro-latest": "gemini-4-argon",
}

# Interactions API のレスポンス形式 (steps / usage) を AI Studio・Vertex AI 間で固定するヘッダ
INTERACTIONS_API_REVISION = "2026-05-20"


def resolve_model(model: str) -> str:
    """AI Studio では -latest エイリアスをそのまま使い、Vertex AI では明示モデルへ解決する"""
    if project():
        return _VERTEX_LATEST_MAP.get(model, model)
    return model


def is_gemini4(model: str) -> bool:
    """Gemini 4 系モデルか (temperature 非推奨・明示キャッシュ非対応・Vertex は global 限定)"""
    resolved = _VERTEX_LATEST_MAP.get(model, model)
    return resolved.startswith("gemini-4-")


def token() -> str:
    """VM のサービスアカウントのアクセストークン (切れる 5 分前まで使い回す)。⚠同期で呼ぶ"""
    global _token
    if _token[0] and time.time() < _token[1]:
        return _token[0]
    req = urllib.request.Request(_METADATA_TOKEN, headers={"Metadata-Flavor": "Google"})
    with urllib.request.urlopen(req, timeout=5) as r:
        d = json.loads(r.read())
    _token = (d["access_token"], time.time() + int(d.get("expires_in", 300)) - 300)
    return _token[0]


def _vertex_host(loc: str) -> str:
    return "aiplatform.googleapis.com" if loc == "global" else f"{loc}-aiplatform.googleapis.com"


def url(path: str) -> str:
    """REST の宛先。path は AI Studio の形 (models/<m>:generateContent / cachedContents / interactions) か、
    Vertex のリソース名 (projects/... = キャッシュの名前) のどちらでもよい"""
    p = project()
    if not p:
        return f"https://generativelanguage.googleapis.com/v1beta/{path}"
    if path.startswith("projects/"):
        return f"https://aiplatform.googleapis.com/v1/{path}"
    if path == "interactions" or path.startswith("interactions/"):
        # Gemini 4 Interactions API は global エンドポイントで提供される (2026-11-13 実測)
        return f"https://aiplatform.googleapis.com/v1/projects/{p}/locations/global/{path}"
    loc = location()
    if path.startswith("models/"):
        rest = path[len("models/") :]
        if ":" in rest:
            m, action = rest.split(":", 1)
            resolved = resolve_model(m)
            rest = f"{resolved}:{action}"
        else:
            resolved = resolve_model(rest)
            rest = resolved
        if is_gemini4(resolved):
            # Gemini 4 系 (hydrogen / argon) はリージョン指定だと 404 になるため常に global へ送る
            loc = "global"
        path = "publishers/google/models/" + rest
    host = _vertex_host(loc)
    return f"https://{host}/v1/projects/{p}/locations/{loc}/{path}"


def model_name(model: str) -> str:
    """キャッシュを作るときの model の書き方"""
    p = project()
    if not p:
        return f"models/{model}"
    return f"projects/{p}/locations/{location()}/publishers/google/models/{resolve_model(model)}"


def headers(*, interactions: bool = False) -> dict[str, str]:
    if project():
        h = {"Content-Type": "application/json", "Authorization": f"Bearer {token()}"}
    else:
        h = {"Content-Type": "application/json", "x-goog-api-key": os.environ.get("GOOGLE_API_KEY", "")}
    if interactions:
        h["Api-Revision"] = INTERACTIONS_API_REVISION
    return h


def available() -> bool:
    return bool(project() or os.environ.get("GOOGLE_API_KEY"))
