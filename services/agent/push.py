# -*- coding: utf-8 -*-
"""FCM (Firebase Cloud Messaging) で端末を起こす (2026-09-18)。

なぜ要るか: アプリは管制室にロングポーリングでぶら下がって着信を知るが、ポケットの携帯は
Doze で止まる (据え置き・常時給電なら要らない)。メイン端末で常用に移すので、着信と取り次ぎの
瞬間だけ Google のプッシュで起こす。

⚠設計: **押すのは「起きろ」だけ**。番号も理由も載せない。起きた端末は今までどおり
  /api/device/state を取りに来る — 判断も内容も hookd が持ったまま、最後の一区間だけ借りる。
  ロングポーリングは残す (プッシュが落ちても据え置き端末は今までどおり動く)。

⚠self-host: Firebase プロジェクトは各自のもの。
  ・送る側 (ここ): サービスアカウント鍵 FCM_SERVICE_ACCOUNT (project_id は鍵から取る)
  ・受ける側 (アプリ): 管制室の /api/device/config が配る FCM_ANDROID_* (lib/env.ts)
  どちらも未設定なら黙って何もしない (ロングポーリングのみ)。

⚠トークンはログインの行 (device_sessions.push_token、2026-10-04)。アプリが /api/device/push で登録し、
  FCM が UNREGISTERED を返したらここで消す (端末のアンインストール・再インストール)。ログアウトでも消える
"""

import asyncio
import json
import logging
import os
import time

import aiohttp

import members

log = logging.getLogger("push")

# ⚠宛先 (FCM トークン) と一時停止はログイン (device_sessions.push_token / paused) に持つ (2026-10-04)。
#   表は members.DDL が作る。以前の device_push_tokens はそこで移して消す
DDL = ""

SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
# 着信は数秒で終わる。届かなかったプッシュを後で配られても鳴らす相手がいない
TTL = "30s"

_sa_path = os.environ.get("FCM_SERVICE_ACCOUNT", "")
_creds = None
_project_id = ""
_warned = False


def _load() -> bool:
    """鍵を一度だけ読む。無ければ False (無効)。"""
    global _creds, _project_id, _warned
    if _creds is not None:
        return True
    if not _sa_path:
        return False
    try:
        from google.oauth2 import service_account  # google-genai 経由で入っている

        _creds = service_account.Credentials.from_service_account_file(_sa_path, scopes=[SCOPE])
        with open(_sa_path, encoding="utf-8") as f:
            _project_id = json.load(f).get("project_id", "")
        log.info("FCM 有効: project=%s", _project_id)
        return True
    except Exception:
        if not _warned:
            _warned = True
            log.exception("FCM の鍵が読めない (%s) — プッシュ無しで続ける", _sa_path)
        return False


def preload() -> None:
    """鍵を先に読んでおく (hookd の起動時に別スレッドから)。無効なら何もしない。
    ⚠トークン更新に使うモジュールの import もここで済ませる。_access_token の中の import は
      初回だけ重く (requests 一式の読み込み)、イベントループを止めてスタンバイの秒数がずれた"""
    if _load():
        import google.auth.transport.requests  # noqa: F401


async def _access_token() -> str:
    from google.auth.transport.requests import Request

    if not _creds.valid:
        # refresh は同期 HTTP。イベントループを止めない
        await asyncio.to_thread(_creds.refresh, Request())
    return _creds.token


async def listeners(pool) -> tuple[int, int]:
    """登録端末の数と、そのうち一時停止していない数 (2026-09-24)。
    ⚠読めなければ (0, 0) = 「端末の登録が無い」扱い。呼び出し側は従来どおりに動く (fail-open)"""
    try:
        row = await pool.fetchrow(
            "SELECT count(DISTINCT s.device_id) AS total, "
            "count(DISTINCT s.device_id) FILTER (WHERE NOT s.paused) AS active "
            f"FROM {members.ACTIVE_SESSIONS} WHERE s.push_token IS NOT NULL"
        )
        return int(row["total"]), int(row["active"])
    except Exception:
        log.exception("起こす宛先の集計に失敗")
        return 0, 0


async def wake(
    pool, kind: str, device_ids: list[str] | None = None, exclude: list[str] | None = None
) -> None:
    """登録済みの端末に「起きろ」を送る。kind は ring / handoff (端末側のログ用)。
    device_ids を渡すとその端末だけ (保留中に相手を指定して呼ぶとき、2026-09-26)。
    exclude の端末には送らない (受付時間外の人、2026-09-29)。

    ⚠失敗しても呼び出し元を止めない (鳴らす経路の本線はロングポーリング)。
    """
    if not _load():
        return
    try:
        if device_ids:
            rows = await pool.fetch(
                f"SELECT DISTINCT s.push_token AS token FROM {members.ACTIVE_SESSIONS} "
                "WHERE s.push_token IS NOT NULL AND NOT s.paused AND s.device_id = ANY($1::text[])",
                device_ids,
            )
        else:
            # 宛先はいま有効なログインのものだけ (ログアウト・締め出しで消える、2026-10-04)
            rows = await pool.fetch(
                f"SELECT DISTINCT s.push_token AS token FROM {members.ACTIVE_SESSIONS} "
                "WHERE s.push_token IS NOT NULL AND NOT s.paused AND NOT s.device_id = ANY($1::text[])",
                [x for x in exclude or [] if x],
            )
    except Exception:
        log.exception("起こす宛先が読めない")
        return
    if not rows:
        return
    try:
        token = await _access_token()
    except Exception:
        log.exception("FCM のアクセストークン取得に失敗")
        return
    url = f"https://fcm.googleapis.com/v1/projects/{_project_id}/messages:send"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    t0 = time.time()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as http:
        results = await asyncio.gather(
            *(_send_one(http, url, headers, pool, r["token"], kind) for r in rows),
            return_exceptions=True,
        )
    ok = sum(1 for r in results if r is True)
    log.info("FCM %s: %d/%d 台に送信 (%.2fs)", kind, ok, len(rows), time.time() - t0)


async def _send_one(http, url, headers, pool, token: str, kind: str) -> bool:
    body = {
        "message": {
            "token": token,
            # データのみ・高優先度。通知型にすると OS が勝手に通知を出し、アプリは起きない
            "android": {"priority": "high", "ttl": TTL},
            "data": {"type": kind, "at": str(int(time.time()))},
        }
    }
    try:
        async with http.post(url, headers=headers, json=body) as res:
            if res.status == 200:
                return True
            text = await res.text()
    except Exception as e:
        log.warning("FCM 送信失敗 (%s…): %s", token[:12], e)
        return False
    # UNREGISTERED (404) / 無効トークン (400 INVALID_ARGUMENT) は登録を消す
    if res.status in (400, 404) and ("UNREGISTERED" in text or "INVALID_ARGUMENT" in text):
        log.info("FCM トークン失効 → 削除 (%s…)", token[:12])
        try:
            await pool.execute("UPDATE device_sessions SET push_token = NULL WHERE push_token = $1", token)
        except Exception:
            log.exception("失効トークンの削除に失敗")
    else:
        log.warning("FCM 送信エラー HTTP %s: %s", res.status, text[:200])
    return False
