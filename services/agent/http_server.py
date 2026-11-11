# -*- coding: utf-8 -*-
"""Cloud Run / HTTP プッシュ駆動モードのコンテキスト (HttpCallCtx) と HTTP サーバー (run_http_server)。"""

import asyncio
import logging
import os

from livekit import api as lk_api
from livekit import rtc
from livekit.agents import AutoSubscribe

from call_db import CallDb
from media_pipeline import _make_live_vad

logger = logging.getLogger("call-agent")


class HttpCallCtx:
    """Cloud Run / HTTP プッシュ駆動モード用の JobContext 互換コンテキスト (2026-11-04)。
    1 HTTP リクエスト = 1 通話のライフサイクルを管理し、ルーム切断または発信者退室まで待機する。"""

    def __init__(self, payload: dict):
        self.room_name: str = payload["room"]
        self.self_identity: str = f"agent-{self.room_name}"
        self.lease_token: str | None = payload.get("lease_token")
        self.caller_override: str | None = payload.get("caller")
        self.is_reentry: bool = bool(payload.get("is_reentry", False))
        self.prompt_overrides: dict[str, str] | None = payload.get("prompts")
        self.urgent_allowed_override: bool | None = payload.get("urgent_allowed")
        self.evicted_by_duplicate: bool = False
        self.room = rtc.Room()
        self.api = lk_api.LiveKitAPI(
            url=os.environ.get("LIVEKIT_URL", "ws://127.0.0.1:7880"),
            api_key=os.environ.get("LIVEKIT_API_KEY", ""),
            api_secret=os.environ.get("LIVEKIT_API_SECRET", ""),
        )
        self._shutdown_cbs: list = []
        self._closed = asyncio.Event()
        self._db: CallDb | None = None
        self._hb_task: asyncio.Task | None = None

        @self.room.on("disconnected")
        def _on_room_disconnected(reason=None):
            if (
                reason == rtc.DisconnectReason.DUPLICATE_IDENTITY
                or "DUPLICATE_IDENTITY" in str(reason or "").upper()
            ):
                self.evicted_by_duplicate = True
                if self._db is not None:
                    self._db.evicted_by_duplicate = True
                    self._db.lease_lost = True
                logger.warning("room disconnected due to DUPLICATE_IDENTITY: %s", self.room_name)
            self._closed.set()

        @self.room.on("participant_disconnected")
        def _on_part_disconnected(p: rtc.RemoteParticipant):
            if self._is_caller_participant(p):
                # 発信者 (SIP / caller-sim) が退室したら通話終了
                if not any(
                    self._is_caller_participant(rem)
                    for rem in self.room.remote_participants.values()
                ):
                    self._closed.set()

    def _is_caller_participant(self, p: rtc.RemoteParticipant) -> bool:
        if p.identity == self.self_identity or p.identity.startswith(("operator-", "watch-", "dashboard-")):
            return False
        return p.kind in (
            rtc.ParticipantKind.PARTICIPANT_KIND_SIP,
            rtc.ParticipantKind.PARTICIPANT_KIND_AGENT,
        )

    def attach_db(self, db: CallDb) -> None:
        self._db = db
        if self._hb_task is None:
            self._hb_task = asyncio.create_task(self._hb_loop())

    async def _hb_loop(self) -> None:
        while not self._closed.is_set():
            await asyncio.sleep(2.0)
            if self._db is not None:
                alive = await self._db.heartbeat()
                if not alive:
                    logger.warning("lease lost during call — closing HttpCallCtx (%s)", self.room_name)
                    self._closed.set()
                    return

    def add_shutdown_callback(self, cb) -> None:
        self._shutdown_cbs.append(cb)

    async def connect(self, auto_subscribe: AutoSubscribe = AutoSubscribe.AUDIO_ONLY) -> None:
        token = (
            lk_api.AccessToken(
                os.environ.get("LIVEKIT_API_KEY", ""),
                os.environ.get("LIVEKIT_API_SECRET", ""),
            )
            .with_identity(self.self_identity)
            .with_name("call-agent")
            .with_kind("agent")
            .with_grants(
                lk_api.VideoGrants(
                    room_join=True,
                    room=self.room_name,
                    agent=True,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                )
            )
            .to_jwt()
        )
        opts = rtc.RoomOptions(
            auto_subscribe=(auto_subscribe == AutoSubscribe.AUDIO_ONLY or auto_subscribe == AutoSubscribe.SUBSCRIBE_ALL)
        )
        await self.room.connect(
            os.environ.get("LIVEKIT_URL", "ws://127.0.0.1:7880"), token, options=opts
        )

    async def wait_for_participant(self, kind: list | None = None) -> rtc.RemoteParticipant:
        def _matches(p: rtc.RemoteParticipant) -> bool:
            if p.identity == self.self_identity or p.identity.startswith(("operator-", "watch-", "dashboard-")):
                return False
            return kind is None or p.kind in kind

        for p in self.room.remote_participants.values():
            if _matches(p):
                return p

        fut: asyncio.Future[rtc.RemoteParticipant] = asyncio.get_running_loop().create_future()

        def _on_join(p: rtc.RemoteParticipant):
            if not fut.done() and _matches(p):
                fut.set_result(p)

        self.room.on("participant_connected", _on_join)
        try:
            return await asyncio.wait_for(fut, timeout=30.0)
        finally:
            try:
                self.room.off("participant_connected", _on_join)
            except Exception:
                pass

    async def wait_for_call_end(self) -> None:
        """ルーム切断・発信者退室・リース喪失のいずれかまで待機する (heartbeat は attach_db 時点で起動済み)。"""
        if self._db is not None and self._db.lease_lost:
            return
        if not any(
            self._is_caller_participant(p)
            for p in self.room.remote_participants.values()
        ):
            await asyncio.sleep(1.0)
            if not any(
                self._is_caller_participant(p)
                for p in self.room.remote_participants.values()
            ):
                return
        await self._closed.wait()

    async def shutdown(self) -> None:
        self._closed.set()
        if self._hb_task is not None:
            self._hb_task.cancel()
            self._hb_task = None
        for cb in reversed(self._shutdown_cbs):
            try:
                res = cb()
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                logger.exception("shutdown callback failed")
        try:
            await self.room.disconnect()
        except Exception:
            pass
        try:
            await self.api.aclose()
        except Exception:
            pass


def run_http_server(entrypoint_fn=None) -> None:
    """Cloud Run / HTTP プッシュモードのサーバーを起動する (AGENT_MODE=http)。"""
    from aiohttp import web
    from livekit.agents.utils import http_context

    if entrypoint_fn is None:
        from agent import entrypoint as entrypoint_fn  # noqa: PLC0415

    # ポートを開く前に VAD をメモリにプリロードしておく (コールドスタート短縮)
    _make_live_vad()
    active_tasks: dict[str, asyncio.Task] = {}
    rev = os.environ.get("K_REVISION", "local")

    async def handle_warmup(_req: web.Request) -> web.Response:
        return web.json_response({"ok": True, "warm": True, "revision": rev})

    async def handle_ready(_req: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def handle_call(req: web.Request) -> web.Response:
        payload = await req.json()
        room_name = payload.get("room", "")
        if not room_name:
            return web.json_response({"ok": False, "error": "missing room"}, status=400)

        # 同一ルームの古いタスクが残っていた場合のみキャンセルして掃除する
        prev = active_tasks.get(room_name)
        if prev is not None and not prev.done():
            logger.warning("cancelling leftover call task for same room %s", room_name)
            prev.cancel()
            try:
                await prev
            except (asyncio.CancelledError, Exception):
                pass

        http_context._new_session_ctx()
        ctx = HttpCallCtx(payload)

        async def _run_call():
            try:
                await entrypoint_fn(ctx)
                await ctx.wait_for_call_end()
            finally:
                await ctx.shutdown()
                try:
                    await http_context._close_http_ctx()
                except Exception:
                    pass

        task = asyncio.create_task(_run_call())
        active_tasks[room_name] = task
        try:
            await task
            return web.json_response({"ok": True, "room": room_name, "revision": rev})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("handle_call error (%s)", room_name)
            return web.json_response({"ok": False, "error": str(e)}, status=500)
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            if active_tasks.get(room_name) is task:
                active_tasks.pop(room_name, None)

    app = web.Application()
    app.router.add_get("/warmup", handle_warmup)
    app.router.add_post("/warmup", handle_warmup)
    app.router.add_get("/ready", handle_ready)
    app.router.add_get("/healthz", handle_ready)
    app.router.add_post("/handle_call", handle_call)
    port = int(os.environ.get("PORT") or os.environ.get("AGENT_HTTP_PORT") or "8080")
    logger.info(
        "starting agent HTTP server on port %d (LIVE_VAD=%s, rev=%s)",
        port,
        os.environ.get("LIVE_VAD", "silero"),
        rev,
    )
    web.run_app(app, host="0.0.0.0", port=port)
