# -*- coding: utf-8 -*-
"""calld — Cloud Run (HTTP プッシュ駆動 agent) のディスパッチ・HTTP保持・死活監視デーモン (2026-11-04)。

責務:
1. 先行起動 (POST /warmup):
   hookd (/ringing_start, /dial, /simulate_call) から呼ばれ、Cloud Run の /warmup を叩いて
   コンテナを録音告知・発信呼び出しの裏でウォームアップする。
   concurrency=1 の Cloud Run で /warmup と /handle_call が競合して2台目がコールドスタートするのを防ぐため、
   _dispatch_room は進行中の _warmup_task があれば完了を待ってから /handle_call を投げる。
2. LiveKit Webhook 受信 (POST /livekit_webhook):
   participant_joined (call_* ルームの発信者: SIP または caller-sim) を受け取り、即座にディスパッチを開始する。
3. 世代トークン (agent_lease) 発行と HTTP 接続保持:
   calls.agent_lease を原子的に取得し、Cloud Run の POST /handle_call を通話終了まで開きっぱなしにする
   (Request-based billing で通話中のみ CPU 課金させるため)。
4. ウォッチドッグ (_watchdog, 3秒周期):
   LiveKit 上でアクティブな call_* ルームを巡回し、agent_heartbeat_at が 6秒以上途絶している場合
   (かつディスパッチ開始から20秒の起動猶予を過ぎている場合) に新しい世代トークンで再ディスパッチする (最大3回)。
"""

import asyncio
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web
import asyncpg
from livekit import api as lk_api
from livekit import rtc

from workspace_config import load_workspace_prompts, urgent_allowed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("calld")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "ws://host.docker.internal:7880")
LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "")
CLOUD_AGENT_URL = os.environ.get("CLOUD_AGENT_URL", "").strip().rstrip("/")
CLOUD_AGENT_IAM_AUTH = os.environ.get("CLOUD_AGENT_IAM_AUTH", "1") == "1"
FRAGMENT_WORKSPACE = Path(os.environ.get("FRAGMENT_WORKSPACE", "/fragment"))
PORT = int(os.environ.get("CALLD_PORT", "8795"))

ROOM_CALLER_RE = re.compile(r"^call_([0-9+]+)_")

# room_name -> (asyncio.Task, started_monotonic)
IN_FLIGHT: dict[str, tuple[asyncio.Task, float]] = {}
# room_name -> retry count
RETRY_COUNTS: dict[str, int] = {}
MAX_RETRIES_PER_ROOM = 3
STARTUP_GRACE_SEC = 35.0
HEARTBEAT_STALE_SEC = 6.0

# 進行中の warmup タスク (concurrency=1 で /handle_call と重ならないよう調整する)
_WARMUP_TASK: asyncio.Task | None = None

# Cloud Run の直近の健康状態 (Asterisk /ai_ready にミリ秒で即答するため)
_HEALTH_STATE = {
    "consecutive_failures": 0,
    "last_failure_at": 0.0,
}

# Cloud Run IAM ID トークンの簡易キャッシュ (有効期限 50分)
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


def _record_cloud_success() -> None:
    _HEALTH_STATE["consecutive_failures"] = 0


def _record_cloud_failure() -> None:
    _HEALTH_STATE["consecutive_failures"] = int(_HEALTH_STATE["consecutive_failures"]) + 1
    _HEALTH_STATE["last_failure_at"] = time.monotonic()


async def _get_id_token(session: aiohttp.ClientSession, audience: str) -> str | None:
    """GCE メタデータサーバーまたは ADC から Cloud Run 呼び出し用の ID トークンを取得する。"""
    if not CLOUD_AGENT_IAM_AUTH or not audience.startswith("https://"):
        return None
    now = time.time()
    cached = _TOKEN_CACHE.get(audience)
    if cached and cached[1] > now:
        return cached[0]
    # 1) GCE メタデータサーバー (169.254.169.254) から非同期で直接取得 (Docker ブリッジ内の DNS 非依存)
    meta_url = (
        "http://169.254.169.254/computeMetadata/v1/instance/"
        f"service-accounts/default/identity?audience={audience}"
    )
    try:
        async with session.get(
            meta_url,
            headers={"Metadata-Flavor": "Google"},
            timeout=aiohttp.ClientTimeout(total=2.0),
        ) as resp:
            if resp.status == 200:
                tok = (await resp.text()).strip()
                if tok:
                    _TOKEN_CACHE[audience] = (tok, now + 3000)
                    return tok
    except Exception:
        pass
    # 2) フォールバック: google-auth
    try:
        import google.auth.transport.requests
        import google.oauth2.id_token

        def _fetch():
            req = google.auth.transport.requests.Request()
            return google.oauth2.id_token.fetch_id_token(req, audience)

        tok = await asyncio.to_thread(_fetch)
        if tok:
            _TOKEN_CACHE[audience] = (tok, now + 3000)
        return tok
    except Exception as e:
        logger.warning("ID token fetch skipped/failed for %s: %s", audience, e)
        return None


def _caller_from_room(room_name: str) -> str | None:
    m = ROOM_CALLER_RE.match(room_name)
    return m.group(1) if m else None


def _is_caller_identity(identity: str, room_name: str) -> bool:
    """自分自身の HTTP agent (`agent-<room_name>`) と管制室/端末 (`operator-` 等) を除外し、
    実着信 (`sip_...`) とテスト相手役 (`caller-sim` = `agent-AJ_...`) を発信者として通す。"""
    if not identity:
        return False
    if identity == f"agent-{room_name}":
        return False
    return not identity.startswith(("operator-", "watch-", "dashboard-"))


async def _ensure_schema(pool: asyncpg.Pool) -> None:
    await pool.execute(
        """ALTER TABLE calls ADD COLUMN IF NOT EXISTS agent_lease text;
           ALTER TABLE calls ADD COLUMN IF NOT EXISTS agent_heartbeat_at timestamptz;"""
    )


async def _dispatch_room(app: web.Application, room_name: str, caller_hint: str | None = None) -> None:
    """1ルーム分の agent_lease 取得 + Cloud Run POST /handle_call の保持を行う。"""
    if not CLOUD_AGENT_URL:
        return
    # 進行中の /warmup があればキャンセルして、コールドスタート中の1台目インスタンスを
    # そのまま /handle_call に引き継がせる (2リクエスト滞留による不要な2台目起動を防ぐ)
    global _WARMUP_TASK
    if _WARMUP_TASK is not None and not _WARMUP_TASK.done():
        _WARMUP_TASK.cancel()
        _WARMUP_TASK = None

    pool: asyncpg.Pool = app["db"]
    http: aiohttp.ClientSession = app["http"]
    caller = caller_hint or _caller_from_room(room_name)
    lease_token = uuid.uuid4().hex

    try:
        # 1. 既存の通話行を確認し、アクティブな別インスタンスが生きている場合は競合を避ける
        existing = await pool.fetchrow(
            """SELECT id, agent_lease,
                      EXTRACT(EPOCH FROM (now() - agent_heartbeat_at)) AS hb_age,
                      EXISTS (SELECT 1 FROM transcript_segments WHERE call_id = calls.id) AS has_segments
               FROM calls WHERE room_name = $1""",
            room_name,
        )
        is_reentry = False
        if existing:
            hb_age = existing["hb_age"]
            if existing["agent_lease"] and hb_age is not None and hb_age < HEARTBEAT_STALE_SEC:
                logger.info("room %s already has active agent (hb_age=%.1fs) — skip dispatch", room_name, hb_age)
                return
            # 実際に会話セグメントが記録されている場合のみ再入室モードにする
            is_reentry = bool(existing["has_segments"])

        # 2. 世代トークン (agent_lease) を原子的に取得・更新
        row = await pool.fetchrow(
            """INSERT INTO calls (room_name, caller_number, agent_lease, agent_heartbeat_at)
               VALUES ($1, $2, $3, now())
               ON CONFLICT (room_name) DO UPDATE
                 SET caller_number = COALESCE(calls.caller_number, EXCLUDED.caller_number),
                     agent_lease = EXCLUDED.agent_lease,
                     agent_heartbeat_at = now(),
                     ended_at = NULL
                 WHERE calls.agent_lease IS NULL
                    OR calls.agent_heartbeat_at IS NULL
                    OR calls.agent_heartbeat_at < now() - interval '6 seconds'
               RETURNING id""",
            room_name,
            caller,
            lease_token,
        )
        if row is None:
            logger.info("room %s lease acquisition skipped (another instance won)", room_name)
            return

        payload = {
            "room": room_name,
            "caller": caller,
            "lease_token": lease_token,
            "is_reentry": is_reentry,
            "prompts": load_workspace_prompts(FRAGMENT_WORKSPACE),
            "urgent_allowed": urgent_allowed(caller, FRAGMENT_WORKSPACE),
        }
        headers: dict[str, str] = {}
        tok = await _get_id_token(http, CLOUD_AGENT_URL)
        if tok:
            headers["Authorization"] = f"Bearer {tok}"

        t2 = time.monotonic()
        logger.info(
            "BENCH T2_dispatch room=%s lease=%s reentry=%s url=%s",
            room_name,
            lease_token[:8],
            is_reentry,
            CLOUD_AGENT_URL,
        )

        # 通話終了まで HTTP 接続を開きっぱなしにする (最大 65分)
        async with http.post(
            f"{CLOUD_AGENT_URL}/handle_call",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=3900, connect=15),
        ) as resp:
            body = await resp.text()
            elapsed = (time.monotonic() - t2) * 1000
            if resp.status >= 400:
                _record_cloud_failure()
                RETRY_COUNTS[room_name] = RETRY_COUNTS.get(room_name, 0) + 1
                logger.error(
                    "handle_call HTTP %d for room=%s (%.0fms, retry=%d): %s",
                    resp.status,
                    room_name,
                    elapsed,
                    RETRY_COUNTS[room_name],
                    body[:300],
                )
            else:
                _record_cloud_success()
                RETRY_COUNTS.pop(room_name, None)
                logger.info(
                    "handle_call completed room=%s status=%d duration_ms=%.0f body=%s",
                    room_name,
                    resp.status,
                    elapsed,
                    body[:160],
                )
    except asyncio.CancelledError:
        logger.info("dispatch task cancelled for room=%s", room_name)
        raise
    except Exception:
        _record_cloud_failure()
        RETRY_COUNTS[room_name] = RETRY_COUNTS.get(room_name, 0) + 1
        logger.exception("dispatch_room failed for room=%s (retry=%d)", room_name, RETRY_COUNTS[room_name])
    finally:
        cur = IN_FLIGHT.get(room_name)
        if cur is not None and cur[0] is asyncio.current_task():
            IN_FLIGHT.pop(room_name, None)


def trigger_dispatch(app: web.Application, room_name: str, caller_hint: str | None = None) -> bool:
    if not room_name.startswith("call_") or not CLOUD_AGENT_URL:
        return False
    cur = IN_FLIGHT.get(room_name)
    if cur is not None and not cur[0].done():
        return False
    if RETRY_COUNTS.get(room_name, 0) >= MAX_RETRIES_PER_ROOM:
        logger.error("room %s exceeded max retries (%d) — giving up dispatch", room_name, MAX_RETRIES_PER_ROOM)
        return False
    task = asyncio.create_task(_dispatch_room(app, room_name, caller_hint))
    IN_FLIGHT[room_name] = (task, time.monotonic())
    return True


async def handle_warmup(req: web.Request) -> web.Response:
    """hookd (/ringing_start, /dial, /simulate_call) から呼ばれ、Cloud Run を先行ウォームアップする。"""
    if not CLOUD_AGENT_URL:
        return web.json_response({"ok": True, "mode": "local"})
    data = {}
    try:
        raw = await req.text()
        if raw:
            data = json.loads(raw)
    except Exception:
        pass
    reason = req.query.get("reason") or data.get("reason", "unknown")
    http: aiohttp.ClientSession = req.app["http"]
    t0 = time.monotonic()

    async def _do_warmup():
        try:
            headers: dict[str, str] = {}
            tok = await _get_id_token(http, CLOUD_AGENT_URL)
            if tok:
                headers["Authorization"] = f"Bearer {tok}"
            async with http.get(
                f"{CLOUD_AGENT_URL}/warmup",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15.0),
            ) as resp:
                await resp.read()
                dt = (time.monotonic() - t0) * 1000
                if resp.status < 400:
                    _record_cloud_success()
                else:
                    _record_cloud_failure()
                logger.info(
                    "BENCH warmup_done reason=%s status=%d T1_minus_T0_ms=%.1f",
                    reason,
                    resp.status,
                    dt,
                )
        except Exception as e:
            _record_cloud_failure()
            logger.warning("warmup request failed (reason=%s): %s", reason, e)

    global _WARMUP_TASK
    if _WARMUP_TASK is None or _WARMUP_TASK.done():
        _WARMUP_TASK = asyncio.create_task(_do_warmup())
    return web.json_response({"ok": True, "warming": True})


async def handle_ai_ready(_req: web.Request) -> web.Response:
    """Asterisk が LiveKit ブリッジ直前に確認する死活エンドポイント。
    ネットワーク待ちによる遅延や 2台目インスタンスのコールドスタートを避けるため、
    直近の連続失敗カウントからミリ秒で即答する。"""
    if not CLOUD_AGENT_URL:
        return web.Response(text="ok")
    fails = int(_HEALTH_STATE["consecutive_failures"])
    age = time.monotonic() - float(_HEALTH_STATE["last_failure_at"])
    if fails >= 3 and age < 60.0:
        return web.Response(text="no")
    return web.Response(text="ok")


async def handle_livekit_webhook(req: web.Request) -> web.Response:
    """LiveKit Server の webhook (participant_joined) を受けて即座にディスパッチする。"""
    try:
        payload = json.loads(await req.text())
    except Exception:
        return web.Response(status=400, text="invalid json")
    event = payload.get("event", "")
    room = payload.get("room") or {}
    room_name = room.get("name", "")
    participant = payload.get("participant") or {}
    identity = participant.get("identity", "")

    if event == "room_finished" and room_name:
        RETRY_COUNTS.pop(room_name, None)
        return web.Response(text="ok")

    if event in ("participant_joined", "room_started") and room_name.startswith("call_"):
        if event == "participant_joined" and not _is_caller_identity(identity, room_name):
            return web.Response(text="ignored_non_caller")
        if event == "participant_joined":
            attrs = participant.get("attributes") or {}
            caller_hint = attrs.get("sip.phoneNumber") or _caller_from_room(room_name)
            dispatched = trigger_dispatch(req.app, room_name, caller_hint)
            logger.info(
                "webhook %s room=%s identity=%s dispatched=%s",
                event,
                room_name,
                identity,
                dispatched,
            )
    return web.Response(text="ok")


async def _watchdog(app: web.Application) -> None:
    """3秒周期でアクティブな call_* ルームを監視し、agent 不在・スタック時に再ディスパッチする。"""
    pool: asyncpg.Pool = app["db"]
    lkapi: lk_api.LiveKitAPI = app["lkapi"]
    while True:
        await asyncio.sleep(3.0)
        if not CLOUD_AGENT_URL or not LIVEKIT_API_KEY:
            continue
        try:
            rooms_resp = await lkapi.room.list_rooms(lk_api.ListRoomsRequest())
            active_room_names = set()
            for rm in rooms_resp.rooms:
                if not rm.name.startswith("call_"):
                    continue
                # 1時間以上前に作られた孤児ルーム (Redis に残った古いゴミ) は削除して無視する
                if rm.creation_time and (time.time() - rm.creation_time) > 3600:
                    logger.warning("watchdog: deleting stale zombie room %s (created %ds ago)", rm.name, int(time.time() - rm.creation_time))
                    try:
                        await lkapi.room.delete_room(lk_api.DeleteRoomRequest(room=rm.name))
                    except Exception:
                        pass
                    continue
                active_room_names.add(rm.name)
                if rm.num_participants == 0:
                    continue
                parts_resp = await lkapi.room.list_participants(
                    lk_api.ListParticipantsRequest(room=rm.name)
                )
                has_caller = any(
                    _is_caller_identity(p.identity, rm.name)
                    and p.kind
                    in (
                        rtc.ParticipantKind.PARTICIPANT_KIND_SIP,
                        rtc.ParticipantKind.PARTICIPANT_KIND_AGENT,
                    )
                    for p in parts_resp.participants
                )
                if not has_caller:
                    continue

                # 進行中のディスパッチがあり、まだ起動猶予期間内 (20秒未満) なら待つ
                in_flight = IN_FLIGHT.get(rm.name)
                if in_flight is not None and not in_flight[0].done():
                    if time.monotonic() - in_flight[1] < STARTUP_GRACE_SEC:
                        continue

                row = await pool.fetchrow(
                    """SELECT agent_lease,
                              EXTRACT(EPOCH FROM (now() - agent_heartbeat_at)) AS hb_age
                       FROM calls WHERE room_name = $1""",
                    rm.name,
                )
                needs_dispatch = (
                    row is None
                    or not row["agent_lease"]
                    or row["hb_age"] is None
                    or row["hb_age"] > HEARTBEAT_STALE_SEC
                )
                if needs_dispatch:
                    if in_flight is not None and not in_flight[0].done():
                        logger.warning(
                            "watchdog: room %s heartbeat stale (hb_age=%s) — cancelling stuck task and re-dispatching",
                            rm.name,
                            row["hb_age"] if row else None,
                        )
                        in_flight[0].cancel()
                        IN_FLIGHT.pop(rm.name, None)
                    trigger_dispatch(app, rm.name)

            # 既に消えたルームの RETRY_COUNTS を掃除
            for rname in list(RETRY_COUNTS.keys()):
                if rname not in active_room_names:
                    RETRY_COUNTS.pop(rname, None)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("watchdog iteration error")


async def on_startup(app: web.Application) -> None:
    app["db"] = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    await _ensure_schema(app["db"])
    app["http"] = aiohttp.ClientSession()
    app["lkapi"] = lk_api.LiveKitAPI(
        url=LIVEKIT_URL,
        api_key=LIVEKIT_API_KEY,
        api_secret=LIVEKIT_API_SECRET,
    )
    app["watchdog"] = asyncio.create_task(_watchdog(app))
    logger.info("calld started on port %d (CLOUD_AGENT_URL=%s)", PORT, CLOUD_AGENT_URL or "<disabled>")


async def on_cleanup(app: web.Application) -> None:
    app["watchdog"].cancel()
    for rname, (task, _) in list(IN_FLIGHT.items()):
        task.cancel()
    await app["lkapi"].aclose()
    await app["http"].close()
    await app["db"].close()


def main() -> None:
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_post("/warmup", handle_warmup)
    app.router.add_get("/warmup", handle_warmup)
    app.router.add_get("/ai_ready", handle_ai_ready)
    app.router.add_post("/livekit_webhook", handle_livekit_webhook)
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
