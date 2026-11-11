# -*- coding: utf-8 -*-
"""通話文字起こしの JSONL バックアップ (TranscriptWriter)、通話履歴 DB 操作 (CallDb)、
および世代トークン検証付きの安全なルーム削除 (safe_delete_room)。
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from itertools import count
from pathlib import Path

import asyncpg
from livekit import api as lk_api

import security

logger = logging.getLogger("call-agent")


def _transcript_dir() -> Path:
    return Path(os.environ.get("TRANSCRIPT_DIR", "/data/transcripts"))


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", "")


class TranscriptWriter:
    """生ログのJSONLバックアップ。通話1件 = 1ファイル、発信者番号ごとのディレクトリ。
    Cloud Run 等でローカルディスクを使わない場合は ENABLE_JSONL_BACKUP=0 で無効化できる。"""

    def __init__(self, room_name: str, caller: str | None):
        self._enabled = os.environ.get("ENABLE_JSONL_BACKUP", "1") != "0"
        self.path: Path | None = None
        if not self._enabled:
            return
        try:
            started = datetime.now(timezone.utc)
            safe_caller = re.sub(r"[^0-9A-Za-z+]", "_", caller or "unknown")
            d = _transcript_dir() / safe_caller
            d.mkdir(parents=True, exist_ok=True)
            safe_room = re.sub(r"[^0-9A-Za-z_-]", "_", room_name)
            self.path = d / f"{started.strftime('%Y%m%dT%H%M%SZ')}_{safe_room}.jsonl"
            self._write(
                {
                    "type": "meta",
                    "event": "call_start",
                    "room": room_name,
                    "caller": caller,
                    "at": started.isoformat(),
                }
            )
            logger.info("transcript -> %s", self.path)
        except Exception:
            logger.exception("TranscriptWriter init failed — JSONL backup disabled")
            self._enabled = False

    def _write(self, obj: dict) -> None:
        if not self._enabled or self.path is None:
            return
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def add_segment(self, speaker: str, text: str, interrupted: bool = False) -> None:
        self._write(
            {
                "type": "segment",
                "speaker": speaker,  # caller(=発信者) / ai
                "text": text,
                "interrupted": interrupted,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def close(self) -> None:
        self._write(
            {"type": "meta", "event": "call_end", "at": datetime.now(timezone.utc).isoformat()}
        )


class CallDb:
    """通話履歴DB (PostgreSQL)。スキーマは infra/postgres/init.sql。
    DB障害で通話を落とさないよう、全操作はエラーを握りつぶしてログに残すだけにする。"""

    def __init__(self, lease_token: str | None = None):
        self._pool: asyncpg.Pool | None = None
        self._listen_conn: asyncpg.Connection | None = None
        self.call_id = None
        self.lease_token = lease_token
        self.lease_lost = False
        self.evicted_by_duplicate = False
        self._seq = count(1)

    async def start_call(self, room: str, caller: str | None) -> bool:
        """通話行を作成または再利用し、再入室 (既存の発話履歴があるか) を返す。"""
        try:
            self._pool = await asyncpg.create_pool(_database_url(), min_size=1, max_size=3)
            if self.lease_token:
                row = await self._pool.fetchrow(
                    """INSERT INTO calls (room_name, caller_number, agent_lease, agent_heartbeat_at)
                       VALUES ($1, $2, $3, now())
                       ON CONFLICT (room_name) DO UPDATE
                         SET caller_number = COALESCE(EXCLUDED.caller_number, calls.caller_number),
                             agent_lease = EXCLUDED.agent_lease,
                             agent_heartbeat_at = now(),
                             ended_at = NULL
                         WHERE calls.agent_lease IS NULL OR calls.agent_lease = EXCLUDED.agent_lease
                       RETURNING id""",
                    room,
                    caller,
                    self.lease_token,
                )
                if row is None:
                    self.lease_lost = True
                    logger.warning(
                        "start_call: agent_lease already replaced for room=%s (mine=%s)",
                        room,
                        self.lease_token[:8],
                    )
                    return False
            else:
                row = await self._pool.fetchrow(
                    """INSERT INTO calls (room_name, caller_number)
                       VALUES ($1, $2)
                       ON CONFLICT (room_name) DO UPDATE
                         SET caller_number = EXCLUDED.caller_number,
                             ended_at = NULL
                       RETURNING id""",
                    room,
                    caller,
                )
            self.call_id = row["id"]
            max_seq = await self._pool.fetchval(
                "SELECT COALESCE(MAX(seq), 0) FROM transcript_segments WHERE call_id = $1",
                self.call_id,
            )
            self._seq = count(int(max_seq or 0) + 1)
            logger.info("call row ready: %s (seq_start=%d)", self.call_id, int(max_seq or 0) + 1)
            return int(max_seq or 0) > 0
        except Exception:
            logger.exception("CallDb.start_call failed (通話は継続)")
            return False

    async def check_lease(self) -> bool:
        """現在のインスタンスが有効な agent_lease を保持しているか確認する。
        ワーカーモード (lease_token=None) では常に True。"""
        if not self.lease_token:
            return True
        if self.lease_lost:
            return False
        if not self._pool or self.call_id is None:
            return False
        try:
            current = await self._pool.fetchval(
                "SELECT agent_lease FROM calls WHERE id = $1", self.call_id
            )
            if current != self.lease_token:
                self.lease_lost = True
                logger.warning(
                    "agent_lease lost (mine=%s current=%s) — 破壊的操作を抑止",
                    self.lease_token[:8],
                    (current or "")[:8],
                )
                return False
            return True
        except Exception:
            return True

    async def heartbeat(self) -> bool:
        """通話中の生存確認タイムスタンプを更新する (2秒周期)。リースを失っていたら False。"""
        if not self.lease_token:
            return True
        if self.lease_lost or not self._pool or self.call_id is None:
            return False
        try:
            updated = await self._pool.fetchval(
                """UPDATE calls SET agent_heartbeat_at = now()
                   WHERE id = $1 AND agent_lease = $2
                   RETURNING id""",
                self.call_id,
                self.lease_token,
            )
            if updated is None:
                self.lease_lost = True
                logger.warning("heartbeat: agent_lease replaced (%s)", self.lease_token[:8])
                return False
            return True
        except Exception:
            return True

    async def fetch_transcript_history(self) -> list[dict]:
        """再入室モード用に直近の発話履歴を取得する。"""
        if not self._pool or self.call_id is None:
            return []
        try:
            rows = await self._pool.fetch(
                """SELECT speaker, text FROM transcript_segments
                   WHERE call_id = $1 AND speaker IN ('caller', 'ai', 'user')
                   ORDER BY seq ASC LIMIT 30""",
                self.call_id,
            )
            return [{"speaker": r["speaker"], "text": r["text"]} for r in rows]
        except Exception:
            logger.exception("fetch_transcript_history failed")
            return []

    async def update_caller(self, caller: str) -> None:
        if not self._pool or self.call_id is None:
            return
        try:
            await self._pool.execute(
                "UPDATE calls SET caller_number = $2 WHERE id = $1", self.call_id, caller
            )
        except Exception:
            logger.exception("CallDb.update_caller failed")

    async def add_segment(self, speaker: str, text: str, interrupted: bool) -> int | None:
        if not self._pool or self.call_id is None:
            return None
        try:
            row = await self._pool.fetchrow(
                """INSERT INTO transcript_segments (call_id, seq, speaker, text, interrupted)
                   VALUES ($1, $2, $3, $4, $5)
                   RETURNING id""",
                self.call_id,
                next(self._seq),
                speaker,
                text,
                interrupted,
            )
            return int(row["id"]) if row else None
        except Exception:
            logger.exception("CallDb.add_segment failed")
            return None

    async def update_segment(self, seg_id: int | None, text: str) -> None:
        if not self._pool or self.call_id is None or seg_id is None:
            return
        try:
            await self._pool.execute(
                "UPDATE transcript_segments SET text = $2 WHERE id = $1 AND call_id = $3",
                seg_id,
                text,
                self.call_id,
            )
        except Exception:
            logger.exception("CallDb.update_segment failed")

    async def search(self, caller: str | None, query: str) -> list[str]:
        if not self._pool:
            return []
        rows = await self._pool.fetch(
            """SELECT c.started_at, ts.speaker, ts.text
               FROM transcript_segments ts JOIN calls c ON c.id = ts.call_id
               WHERE ts.text ILIKE '%' || $1 || '%'
                 AND ($2::text IS NULL OR c.caller_number = $2)
                 AND c.id <> $3
               ORDER BY ts.at DESC LIMIT 10""",
            query,
            caller,
            self.call_id,
        )
        return [
            f"[{r['started_at']:%Y-%m-%d %H:%M}] {r['speaker']}: {r['text']}"
            for r in reversed(rows)
        ]

    async def create_job(self, kind: str, query: str) -> int | None:
        """Directory Agent (別プロセスのworker) へのジョブ投入。"""
        if not self._pool:
            return None
        row = await self._pool.fetchrow(
            "INSERT INTO agent_jobs (call_id, kind, query) VALUES ($1, $2, $3) RETURNING id",
            self.call_id,
            kind,
            query,
        )
        return row["id"]

    async def get_job(self, job_id: int) -> tuple[str, str | None]:
        row = await self._pool.fetchrow(
            "SELECT status, result FROM agent_jobs WHERE id = $1", job_id
        )
        return (row["status"], row["result"]) if row else ("error", None)

    async def find_done_job(self, query: str) -> str | None:
        """同一通話内で同じ質問が完了済みならその結果を返す (タイムアウト後の再問い合わせ用)。"""
        if not self._pool or self.call_id is None:
            return None
        row = await self._pool.fetchrow(
            """SELECT result FROM agent_jobs
               WHERE call_id = $1 AND query = $2 AND status = 'done'
               ORDER BY id DESC LIMIT 1""",
            self.call_id,
            query,
        )
        return row["result"] if row else None

    async def find_recent_done_job(self, query: str) -> str | None:
        """直近3分以内の完了済みジョブを call_id 不問で探す。

        着信フック (hookd) 経由の下調べジョブは INVITE 時点で投入されるため
        call_id を持たない — 入室後のagentはこれで拾う。"""
        if not self._pool:
            return None
        row = await self._pool.fetchrow(
            """SELECT result FROM agent_jobs
               WHERE query = $1 AND status = 'done'
                 AND finished_at > now() - interval '3 minutes'
               ORDER BY id DESC LIMIT 1""",
            query,
        )
        return row["result"] if row else None

    async def fetch_new_whispers(self, after_id: int) -> list:
        """管制室が会話ストリーム (transcript_segments) に直接挿入した耳打ちの新着。
        会話ストリームがバス — ダッシュボードが書き、UI/watcher/アシスタントが購読する。"""
        if not self._pool or self.call_id is None:
            return []
        return await self._pool.fetch(
            """SELECT id, text FROM transcript_segments
               WHERE call_id = $1 AND speaker = 'whisper' AND id > $2 ORDER BY id""",
            self.call_id,
            after_id,
        )

    async def listen_push(self, on_push) -> None:
        """agent_push チャネルの LISTEN (payload = call_id)。耳打ち・後方支援メモを
        NOTIFY で即時配達する — 2秒ポーリングの待ちを潰す (ポーリングは保険で継続)。
        LISTEN はコネクション単位なので専用の直結コネクションを1本持つ。"""
        if self.call_id is None:
            return
        try:
            self._listen_conn = await asyncpg.connect(_database_url())

            def _notified(conn, pid, channel, payload):
                if payload == str(self.call_id):
                    on_push()

            await self._listen_conn.add_listener("agent_push", _notified)
        except Exception:
            logger.exception("listen_push failed (2秒ポーリングで動作継続)")

    async def set_ai_on(self, on: bool) -> None:
        if self.call_id is None or not self._pool:
            return
        try:
            await self._pool.execute("UPDATE calls SET ai_on = $2 WHERE id = $1", self.call_id, on)
        except Exception:
            logger.exception("ai_on の記録に失敗")

    async def get_ai_on(self) -> bool | None:
        """端末が切り替えた AI 応答 (/api/calls/<id>/ai)。読めなければ None"""
        if self.call_id is None or not self._pool:
            return None
        try:
            return await self._pool.fetchval("SELECT ai_on FROM calls WHERE id = $1", self.call_id)
        except Exception:
            return None

    async def set_handler(self, handler: str, presence: list[dict]) -> None:
        """担い手と参加者を端末に見せる。hold_since は保留に入った時刻 (保留が続く間は据え置き)"""
        if self.call_id is None or not self._pool:
            return
        # 会話した人の名前を通話の記録に足していく (2026-10-04)。presence は終話で空にするので、
        # これが無いと「誰が出たか」が後から分からなかった (履歴では人の発話が全部「本人」だった)
        talkers = [p["name"] for p in presence if p.get("role") == "talk" and p.get("name")]
        try:
            await self._pool.execute(
                """UPDATE calls SET handler = $2, presence = $3::jsonb,
                     hold_since = CASE WHEN $2 = 'hold' THEN COALESCE(hold_since, now()) END,
                     handled_by = (SELECT coalesce(jsonb_agg(DISTINCT v), '[]'::jsonb) FROM (
                       SELECT jsonb_array_elements_text(coalesce(handled_by, '[]'::jsonb)) AS v
                       UNION SELECT unnest($4::text[])) x)
                   WHERE id = $1""",
                self.call_id,
                handler,
                json.dumps(presence, ensure_ascii=False),
                talkers,
            )
        except Exception:
            logger.exception("担い手の記録に失敗")

    async def outbound_intent(self, number: str) -> bool:
        """発信の印 (hookd /dial が置く)。この番号への直近の発信通話ならTrue (2026-07-19)。"""
        if not self._pool:
            return False
        try:
            row = await self._pool.fetchrow(
                """SELECT 1 FROM agent_jobs
                   WHERE kind = 'outbound_call' AND query = $1
                     AND created_at > now() - interval '3 minutes' LIMIT 1""",
                number,
            )
            return row is not None
        except Exception:
            logger.exception("CallDb.outbound_intent failed")
            return False

    async def mark_outbound(self, callee: str) -> None:
        if not self._pool or self.call_id is None:
            return
        try:
            await self._pool.execute(
                """UPDATE calls SET direction = 'outbound', callee_number = $2,
                       answered_by = 'human' WHERE id = $1""",
                self.call_id,
                callee,
            )
        except Exception:
            logger.exception("CallDb.mark_outbound failed")

    async def assistant_enabled(self) -> bool:
        """留守電トグル。false でも通話がスタックを通る限り書記 (文字起こし+吹き出し) は動く —
        このフラグが制御するのは「AIが喋るかどうか」だけ (支援機能とは独立、2026-07-18設計)。"""
        if not self._pool:
            return True
        row = await self._pool.fetchrow(
            "SELECT value FROM settings WHERE key = 'assistant_enabled'"
        )
        return row is None or row["value"] != "false"

    async def setting(self, key: str) -> str:
        """settings の 1 行。無い・読めないときは空文字"""
        if not self._pool:
            return ""
        try:
            v = await self._pool.fetchval("SELECT value FROM settings WHERE key = $1", key)
            return (v or "").strip()
        except Exception:
            logger.exception("CallDb.setting(%s) failed", key)
            return ""

    async def tts_providers(self) -> tuple[str | None, str | None]:
        """管制室の設定画面で選べる声の「メイン / サブ」。settings テーブルから読む。
        通話ごとに読むので、切り替えは**次の通話から**効く (コンテナ再起動は不要)。
        行が無ければ (None, None) を返し、呼び出し側が環境変数の既定にフォールバックする —
        既存DBにマイグレーションを当てなくても動くようにするため。"""
        if not self._pool:
            return None, None
        try:
            rows = await self._pool.fetch(
                "SELECT key, value FROM settings WHERE key IN ('tts_primary', 'tts_fallback')"
            )
            got = {r["key"]: (r["value"] or "").strip() for r in rows}
            return got.get("tts_primary") or None, got.get("tts_fallback") or None
        except Exception:
            logger.exception("CallDb.tts_providers failed — 環境変数の既定を使う")
            return None, None

    async def fetch_new_notes(self, after_id: int) -> list:
        """後方支援メモ (workerが会話を監視して自発的に投入する agent_note) の新着を取る。"""
        if not self._pool or self.call_id is None:
            return []
        return await self._pool.fetch(
            """SELECT id, result FROM agent_jobs
               WHERE call_id = $1 AND kind = 'agent_note' AND status = 'done' AND id > $2
               ORDER BY id""",
            self.call_id,
            after_id,
        )

    async def security_check(self, caller: str | None, callee: str | None):
        """SIP不正利用の判定 (security.py)。Asterisk経由なら hookd /guard で既に落ちている —
        ここに来るのは**FWやtrunkのallowed_addressesをすり抜けて直接LiveKitに入った呼**で、
        2026-07-26に実際に起きた経路。判定はセッション生成前なのでSTT/TTS/LLMは1トークンも使わない。
        DB不調時は None を返す = 通話を通す (警備が着信を止める方が損害が大きい)。"""
        if not self._pool:
            return None
        try:
            await security.ensure_schema(self._pool)
            return await security.evaluate(self._pool, caller, callee, source="agent")
        except Exception:
            logger.exception("security_check failed (通話は継続 — fail-open)")
            return None

    async def drop_blocked_call(self) -> None:
        """ブロックした呼の通話行を消す。証跡は security_events / call_attempts が持つので、
        履歴・電話帳・通話記録mdを攻撃で汚さない (2026-07-26は108件の手作業掃除が発生した)。"""
        if not self._pool or self.call_id is None:
            return
        if not await self.check_lease():
            return
        try:
            await self._pool.execute("DELETE FROM calls WHERE id = $1", self.call_id)
            self.call_id = None
        except Exception:
            logger.exception("CallDb.drop_blocked_call failed")

    async def end_call(self) -> None:
        conn, self._listen_conn = self._listen_conn, None
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass
        if not self._pool:
            return
        try:
            if (
                self.call_id is not None
                and not self.evicted_by_duplicate
                and not self.lease_lost
                and await self.check_lease()
            ):
                # 参加者と保留の印は通話中だけのもの。担い手 (handler) は最後の状態を記録として残す
                if self.lease_token:
                    await self._pool.execute(
                        "UPDATE calls SET ended_at = now(), presence = '[]'::jsonb, hold_since = NULL"
                        " WHERE id = $1 AND agent_lease = $2",
                        self.call_id,
                        self.lease_token,
                    )
                else:
                    await self._pool.execute(
                        "UPDATE calls SET ended_at = now(), presence = '[]'::jsonb, hold_since = NULL"
                        " WHERE id = $1",
                        self.call_id,
                    )
            elif self.evicted_by_duplicate:
                logger.warning("skip CallDb.end_call: evicted by DUPLICATE_IDENTITY (call_id=%s)", self.call_id)
        except Exception:
            logger.exception("CallDb.end_call failed")
        finally:
            pool, self._pool = self._pool, None  # 冪等化 (close経路とshutdown経路の二重呼び対策)
            await pool.close()


async def safe_delete_room(ctx, db: CallDb) -> None:
    """世代トークン (agent_lease) が有効かつ DUPLICATE_IDENTITY で追い出されていない場合のみ
    ルームを削除する (2026-11-04、凍結インスタンスの解凍による現役ルーム誤削除対策)。"""
    rname = getattr(ctx, "room_name", None) or ctx.room.name
    if not rname:
        return
    if getattr(ctx, "evicted_by_duplicate", False):
        logger.warning("skip delete_room: evicted by DUPLICATE_IDENTITY (%s)", rname)
        return
    if not await db.check_lease():
        logger.warning("skip delete_room: lease token mismatch (%s)", rname)
        return
    try:
        await ctx.api.room.delete_room(lk_api.DeleteRoomRequest(room=rname))
    except Exception:
        pass


_safe_delete_room = safe_delete_room
