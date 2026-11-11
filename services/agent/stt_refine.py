# -*- coding: utf-8 -*-
"""リアルタイム STT の Gemini 音声補正 (_refine_transcript_with_audio)、
発信者音声 PCM バッファ監視 (watch_caller_track)、および本人 (operator) 音声の
STT ストリーム自動張り直し＋補正 (transcribe_operator_track)。
"""

import asyncio
import io
import logging
import os
import re
import wave

from google.genai import types as genai_types
from livekit import rtc
from livekit.agents import stt as lk_stt

import vertex
from call_db import CallDb, TranscriptWriter
from media_pipeline import _make_stt

logger = logging.getLogger("call-agent")

_REFINE_CLIENT = None


def _get_refine_client():
    global _REFINE_CLIENT
    if _REFINE_CLIENT is None:
        from google import genai

        p = vertex.project()
        if p:
            loc = os.environ.get("VERTEX_STT_REFINE_LOCATION") or vertex.location()
            _REFINE_CLIENT = genai.Client(vertexai=True, project=p, location=loc)
        else:
            _REFINE_CLIENT = genai.Client()
    return _REFINE_CLIENT


def _pcm16k_to_wav(pcm: bytes, sr: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return buf.getvalue()


async def _refine_transcript_with_audio(
    wav_bytes: bytes,
    raw_text: str,
    history: list[dict],
) -> str | None:
    """発話区間の音声(WAV)と初回STTテキスト・直前の会話履歴を Gemini Flash (gemini-flash-latest /
    Vertex 時は gemini-3.8-flash) に渡し、同音異義語や音素の誤認識を補正したテキストを返す。"""
    if not wav_bytes or len(wav_bytes) < 12844 or not raw_text.strip():
        return None
    try:
        import numpy as np

        pcm_arr = np.frombuffer(wav_bytes[44:], dtype=np.int16).astype(np.float32)
        if len(pcm_arr) < 6400 or float(np.sqrt(np.mean(pcm_arr ** 2))) < 60.0:
            return None
        client = _get_refine_client()
        ctx_lines = [
            f"{'相手' if h['speaker'] == 'caller' else '本人' if h['speaker'] == 'user' else 'AI'}: {h['text']}"
            for h in history[-6:]
        ]
        ctx_str = "\n".join(ctx_lines) if ctx_lines else "(なし)"
        prompt = (
            f"直前の会話:\n{ctx_str}\n\n"
            "添付の通話音声を聴き、話者が実際に発話した日本語のセリフのみを1行で正確に書き起こしてください。\n"
            f"（参考: 下位STTの仮認識は「{raw_text}」です。仮認識がすでに自然な日本語として成立しており音声とも矛盾しない箇所は、"
            "勝手に別の言い回しや定型句へ書き換えずそのまま維持してください。"
            "電話回線(8kHz)やマイクのノイズゲートで発話冒頭1〜2音が欠落して別単語に化けている箇所や、"
            "音声と照らし合わせて明確な音素の誤認識・同音異義語ミスがある箇所のみを本来の発話に修正してください）"
        )
        raw_model = os.environ.get("GEMINI_STT_REFINE_MODEL", "gemini-flash-latest")
        model_name = vertex.resolve_model(raw_model)
        contents = [
            genai_types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav"),
            prompt,
        ]
        cfg = genai_types.GenerateContentConfig(
            temperature=0.1,
            # ⚠3.8-flash 以降は minimal 非対応 (400 INVALID_ARGUMENT) のため low で統一 (2026-11-09実測)
            thinking_config=genai_types.ThinkingConfig(thinking_level="low"),
        )
        try:
            resp = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=cfg,
                ),
                timeout=8.0,
            )
        except Exception:
            if model_name == "gemini-3.8-flash":
                raise
            resp = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=contents,
                    config=cfg,
                ),
                timeout=8.0,
            )
        txt = (resp.text or "").strip()
        out = txt.splitlines()[0].strip() if txt else ""
        if out and len(out) <= max(len(raw_text) * 3, 40):
            if raw_text.strip().endswith("。") and not out.endswith(("。", "！", "？", "!", "?")):
                out += "。"
            norm_re = r"[\s　、。！？!?.,「」『』()（）…ー〜]"
            if re.sub(norm_re, "", out) == re.sub(norm_re, "", raw_text):
                return None
            return out
    except Exception as e:
        logger.info("STT音声補正スキップ: %r", e)
    return None


def watch_caller_track(
    track: rtc.RemoteTrack,
    identity: str,
    *,
    caller_pcm: bytearray,
    caller_in_speech: dict,
    caller_tracks_watched: set[str],
) -> None:
    """発信者 (caller) の音声トラックを 16kHz mono PCM として直近バッファに蓄える。"""
    track_sid = getattr(track, "sid", "") or f"{identity}:{id(track)}"
    if track_sid in caller_tracks_watched:
        return
    caller_tracks_watched.add(track_sid)

    async def run_caller_buf():
        audio = rtc.AudioStream(track, sample_rate=16000, num_channels=1)
        try:
            async for ev in audio:
                caller_pcm.extend(bytes(ev.frame.data))
                max_bytes = int(16000 * 2 * (15.0 if caller_in_speech["active"] else 2.5))
                if len(caller_pcm) > max_bytes:
                    del caller_pcm[: len(caller_pcm) - max_bytes]
        except Exception:
            pass
        finally:
            caller_tracks_watched.discard(track_sid)

    asyncio.create_task(run_caller_buf())


def transcribe_operator_track(
    track: rtc.RemoteTrack,
    identity: str,
    *,
    room: rtc.Room,
    db: CallDb,
    writer: TranscriptWriter,
    transcribing_tracks: set[str],
    operator_stop_events: list[asyncio.Event],
) -> None:
    """本人 (operator) の音声を専用 STT ストリームで文字起こしし、speaker='user' として記録・音声補正する。
    本人 (operator) の声は AgentSession の入力対象外 (sessionは発信者の音声だけを聴く) —
    専用のSTTストリームで文字起こしして speaker='user' として記録する (2026-07-18)。
    これで交代後・書記モードの本人発話も画面/DB/吹き出し支援(watcher)に届く。"""
    track_sid = getattr(track, "sid", "") or f"{identity}:{id(track)}"
    if track_sid in transcribing_tracks:
        return
    transcribing_tracks.add(track_sid)
    logger.info("operator音声を購読 — 本人の文字起こし開始 (%s, track=%s)", identity, track_sid)

    async def run():
        # AudioStreamは通話中1本だけ持ち、STTストリームは落ちても張り直す。
        # マイク音声をpumpが「今のSTTストリーム」に流し込む (差し替え可能な参照経由)
        audio = rtc.AudioStream(track, sample_rate=16000, num_channels=1)
        current: dict = {"stream": None}
        stopped = asyncio.Event()
        operator_stop_events.append(stopped)
        utt_pcm = bytearray()
        in_speech = {"active": False}

        async def pump():
            n = 0
            try:
                async for ev in audio:
                    n += 1
                    if n == 1 or n % 500 == 0:
                        logger.info("operator音声 frame#%d (%s)", n, identity)
                    utt_pcm.extend(bytes(ev.frame.data))
                    # Google STT の初回 interim まで 1.5〜2秒かかるため無音時も直近6秒を残して語頭切れを防ぐ
                    max_b = int(16000 * 2 * (15.0 if in_speech["active"] else 6.0))
                    if len(utt_pcm) > max_b:
                        del utt_pcm[: len(utt_pcm) - max_b]
                    s = current["stream"]
                    if s is not None:
                        try:
                            s.push_frame(ev.frame)
                        except Exception:
                            pass  # 差し替えの隙間はフレームを捨てるだけ
            finally:
                logger.info("operator音声 pump終了 (frames=%d, %s)", n, identity)
                stopped.set()  # トラック終了 (operator退室/通話終了) = 本人発話の終わり
                s = current["stream"]
                if s is not None:
                    try:
                        s.end_input()
                    except Exception:
                        pass

        pump_task = asyncio.create_task(pump())
        # STT張り直しループ: 本人が沈黙する時間帯があると Google Cloud STT が
        # Audio Timeout で確定クラッシュする (2026-07-19実機。「拾われてない」の真因は
        # 精度ではなく、一度落ちたSTTストリームが二度と復活しなかったこと)。
        # プロバイダは _make_stt() のまま (STT_PROVIDER設定を発信者用と共通で使う) —
        # 効くのは「落ちても即座に新しいストリームを張り直す」ことで、これはどの
        # プロバイダでも要る保険
        seg_seq = 0
        try:
            while not stopped.is_set():
                stream = _make_stt().stream()
                current["stream"] = stream
                seg_id = f"op-{track_sid}-{seg_seq}"
                try:
                    async for ev in stream:
                        if ev.type == lk_stt.SpeechEventType.START_OF_SPEECH:
                            in_speech["active"] = True
                            continue
                        if not ev.alternatives:
                            continue
                        text = ev.alternatives[0].text.strip()
                        if not text:
                            continue
                        if ev.type == lk_stt.SpeechEventType.INTERIM_TRANSCRIPT:
                            in_speech["active"] = True
                            try:
                                await room.local_participant.send_text(
                                    text,
                                    topic="lk.transcription",
                                    attributes={
                                        "lk.transcribed_track_id": track_sid,
                                        "lk.segment_id": seg_id,
                                        "lk.transcription_final": "false",
                                    },
                                )
                            except Exception:
                                pass
                        elif ev.type == lk_stt.SpeechEventType.FINAL_TRANSCRIPT:
                            logger.info("operator STT final (%s): %r", identity, text[:40])
                            wav_b = _pcm16k_to_wav(bytes(utt_pcm)) if utt_pcm else b""
                            utt_pcm.clear()
                            in_speech["active"] = False
                            cur_seg_id = seg_id
                            seg_seq += 1
                            seg_id = f"op-{track_sid}-{seg_seq}"
                            try:
                                await room.local_participant.send_text(
                                    text,
                                    topic="lk.transcription",
                                    attributes={
                                        "lk.transcribed_track_id": track_sid,
                                        "lk.segment_id": cur_seg_id,
                                        "lk.transcription_final": "true",
                                    },
                                )
                            except Exception:
                                pass
                            writer.add_segment("user", text, False)
                            row_id = await db.add_segment("user", text, False)

                            async def _refine_op(
                                r_id: int | None, lk_sid: str, raw_t: str, w_bytes: bytes
                            ):
                                hist = await db.fetch_transcript_history()
                                refined = await _refine_transcript_with_audio(w_bytes, raw_t, hist)
                                if refined and refined != raw_t:
                                    logger.info("operator STT補正 (%s): %r -> %r", identity, raw_t, refined)
                                    # ⚠CallAudio.interim に残っている補正前テキストを空白で上書き消去してから
                                    #   DB を更新する (CallScreen.kt の isNotBlank() フィルタで即座に非表示になるため、
                                    #   DB 更新後に古い interim と新しい segment が二重表示されるのを防ぐ)
                                    try:
                                        await room.local_participant.send_text(
                                            " ",
                                            topic="lk.transcription",
                                            attributes={
                                                "lk.transcribed_track_id": track_sid,
                                                "lk.segment_id": lk_sid,
                                                "lk.transcription_final": "true",
                                            },
                                        )
                                    except Exception:
                                        pass
                                    await db.update_segment(r_id, refined)

                            if wav_b:
                                asyncio.create_task(_refine_op(row_id, cur_seg_id, text, wav_b))
                except Exception as e:
                    logger.info("operator STT張り直し (%s): %s", identity, type(e).__name__)
                finally:
                    current["stream"] = None
                    try:
                        await stream.aclose()
                    except Exception:
                        pass
                if (
                    not stopped.is_set()
                    and room.connection_state == rtc.ConnectionState.CONN_CONNECTED
                ):
                    await asyncio.sleep(0.3)  # 軽いバックオフ (張り直しの暴走防止)
                else:
                    break
        finally:
            pump_task.cancel()
            transcribing_tracks.discard(track_sid)
            logger.info("operator文字起こし終了 (%s)", identity)

    asyncio.create_task(run())


class CallSttTracker:
    """1 通話分の発信者 PCM 先行補正状態と本人 (operator) 音声トラック購読を一元管理するヘルパー。"""

    def __init__(self, room: rtc.Room, db: CallDb, writer: TranscriptWriter):
        self.room = room
        self.db = db
        self.writer = writer
        # 発信者(caller)音声の直近バッファ (16kHz mono s16le)。
        # ⚠AI_CORE=live では conversation_item_added (on_item) が発火するまでに発話終了から 3.5〜5秒
        #   (AIの裏生成完了待ち) かかるため、on_item で切り出すと次の発話が混ざる＆次の発話の前半が消える。
        #   そのため発話終了直後 (+0.35s) に届く user_input_transcribed(is_final=False) で即座に切り出して
        #   先行補正タスクを走らせ、on_item 時点では待ち時間ゼロで補正済みテキストを採用する (2026-11-09)
        self.caller_pcm = bytearray()
        self.caller_in_speech = {"active": False}
        self.caller_early_refines: list[tuple[str, bytes, asyncio.Task]] = []
        self.caller_tracks_watched: set[str] = set()
        self.transcribing_tracks: set[str] = set()
        self.operator_stop_events: list[asyncio.Event] = []

    def watch_caller(self, track: rtc.RemoteTrack, identity: str) -> None:
        watch_caller_track(
            track,
            identity,
            caller_pcm=self.caller_pcm,
            caller_in_speech=self.caller_in_speech,
            caller_tracks_watched=self.caller_tracks_watched,
        )

    def transcribe_operator(self, track: rtc.RemoteTrack, identity: str) -> None:
        transcribe_operator_track(
            track,
            identity,
            room=self.room,
            db=self.db,
            writer=self.writer,
            transcribing_tracks=self.transcribing_tracks,
            operator_stop_events=self.operator_stop_events,
        )

    def sync_tracks(self, caller_identity: str) -> None:
        """既に入室・購読済みの operator- / caller 音声トラックがあれば文字起こし・録音バッファを開始する。
        ⚠発信時や本人が即応対した着信では、ctx.connect()〜wait_for_participant() の間に
          トラック購読が完了しており、後から登録した track_subscribed が
          発火しないため、既存のトラックも明示的に走査する (2026-11-07)。"""
        for p in self.room.remote_participants.values():
            if p.identity.startswith("operator-"):
                for pub in p.track_publications.values():
                    if pub.track is not None and pub.track.kind == rtc.TrackKind.KIND_AUDIO:
                        self.transcribe_operator(pub.track, p.identity)
            elif p.identity == caller_identity or p.identity.startswith("sip_"):
                for pub in p.track_publications.values():
                    if pub.track is not None and pub.track.kind == rtc.TrackKind.KIND_AUDIO:
                        self.watch_caller(pub.track, p.identity)

    def handle_track_subscribed(
        self, track: rtc.RemoteTrack, p: rtc.RemoteParticipant, caller_identity: str
    ) -> None:
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            return
        if p.identity.startswith("operator-"):
            self.transcribe_operator(track, p.identity)
        elif p.identity == caller_identity or p.identity.startswith("sip_"):
            self.watch_caller(track, p.identity)

    def on_caller_speaking(self) -> None:
        self.caller_in_speech["active"] = True

    def on_caller_interim_transcribed(self, transcript: str) -> None:
        if (
            os.environ.get("AI_CORE", "live") != "cascade"
            and transcript
            and len(self.caller_pcm) >= 12800
        ):
            wav_b = _pcm16k_to_wav(bytes(self.caller_pcm))
            self.caller_pcm.clear()
            self.caller_in_speech["active"] = False

            async def _early_refine(wb: bytes, raw_t: str) -> str | None:
                hist = await self.db.fetch_transcript_history()
                return await _refine_transcript_with_audio(wb, raw_t, hist)

            if len(self.caller_early_refines) >= 8:
                self.caller_early_refines.pop(0)
            self.caller_early_refines.append(
                (transcript, wav_b, asyncio.create_task(_early_refine(wav_b, transcript)))
            )

    def record_conversation_item(self, speaker: str, raw_text: str, interrupted: bool) -> None:
        early_task: asyncio.Task | None = None
        wav_snapshot = b""
        if speaker == "caller":
            norm = raw_text.strip()
            matched_idx = next(
                (i for i, (t, _, _) in enumerate(self.caller_early_refines) if t == norm),
                0 if self.caller_early_refines else None,
            )
            if matched_idx is not None:
                _, wav_snapshot, early_task = self.caller_early_refines.pop(matched_idx)
            elif self.caller_pcm:
                wav_snapshot = _pcm16k_to_wav(bytes(self.caller_pcm))
                self.caller_pcm.clear()
                self.caller_in_speech["active"] = False

        pre_refined: str | None = None
        if early_task is not None and early_task.done() and not early_task.cancelled():
            try:
                res = early_task.result()
                if res and res != raw_text:
                    logger.info("caller STT先行補正完了: %r -> %r", raw_text, res)
                    pre_refined = res
                early_task = None
                wav_snapshot = b""
            except Exception:
                early_task = None

        self.writer.add_segment(speaker, pre_refined or raw_text, interrupted)

        async def _save_item(
            sp: str,
            txt: str,
            intr: bool,
            wav_b: bytes,
            pend_task: asyncio.Task | None,
            already_refined: str | None,
        ):
            # ⚠Android 側の CallAudio.interim は lk.transcription (final=true, text=raw_text) を
            #   3秒間保持し、DB の segments と文字列一致 (it.text.trim() in done) で重複を消す。
            #   最初から補正後テキストで INSERT すると interim の raw_text と一致せず3秒間二重表示されるため、
            #   まず raw_text で INSERT し、interim の保持期限 (3.0s) が切れた直後に UPDATE する
            t_item = asyncio.get_running_loop().time()
            row_id = await self.db.add_segment(sp, txt, intr)
            if sp == "caller":
                refined = already_refined
                if refined is None:
                    if pend_task is not None:
                        try:
                            refined = await pend_task
                        except Exception:
                            refined = None
                    elif wav_b:
                        hist = await self.db.fetch_transcript_history()
                        refined = await _refine_transcript_with_audio(wav_b, txt, hist)
                if refined and refined != txt:
                    if already_refined is None:
                        logger.info("caller STT補正: %r -> %r", txt, refined)
                    rem = 3.1 - (asyncio.get_running_loop().time() - t_item)
                    if rem > 0:
                        await asyncio.sleep(rem)
                    await self.db.update_segment(row_id, refined)

        asyncio.create_task(
            _save_item(speaker, raw_text, interrupted, wav_snapshot, early_task, pre_refined)
        )

    def stop_all(self) -> None:
        for ev in self.operator_stop_events:
            ev.set()
