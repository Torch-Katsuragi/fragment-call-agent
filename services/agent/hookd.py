# -*- coding: utf-8 -*-
"""着信フックAPI — Asteriskのダイヤルプランから INVITE 到着の瞬間に叩かれる。

呼び出し音を鳴らしている間 (extensions.conf の Wait) に caller_context ジョブを
キューへ投入し、directory-agent worker に相手の下調べ (電話帳md + 直近の通話) を
済ませてもらう。agent は入室後に完了済みジョブを拾ってプロンプトへ注入する。

エンドポイント: GET /prefetch?number=<発信者番号>
compose の hookd サービスとして起動 (agent と同じイメージ、command 差し替え)。
"""

import asyncio
import json
import logging
import os
import re
import time

import aiohttp
import asyncpg
from aiohttp import web
from livekit import api as lk_api

import push
import security

DSN = os.environ["DATABASE_URL"]
log = logging.getLogger("hookd")

SIM_NUMBER = security.SIM_NUMBER  # テスト通話のダミー番号 (電話帳: 佐藤 テスト用ダミー)

# 着信中の呼の状態 (番号 → 状態)。留守電OFF時の「管制室で受話」に使う:
# Asterisk が /ringing_start で登録 → 管制室が /ringing_state を表示 →
# 受話ボタンが /pickup_request → Asterisk のダイヤルプランが /pickup で検知して LiveKit にブリッジ
RINGING: dict[str, dict] = {}


# 取り次ぎ要求 (ルーム名 → 状態)。2026-07-31。
#
# ⚠上の RINGING とは別物。RINGING は「まだAIが出ていない着信」を管制室で受話する経路。
#   こちらは**すでにAIが応対中の通話**に本人を呼び込む経路で、通話は成立済み。
#
# ⚠将来スマホアプリが受け手になる。だから状態は「誰が受けるか」を持たず、
#   要求と承諾だけを表す — ブラウザでもアプリでも同じ口を使えるようにしておく。
#
# 流れ: AIが request_handoff ツールを呼ぶ → /handoff_request で登録 →
#       受け手 (管制室 or アプリ) が鳴らして /handoff_state で見張る →
#       応答すると /handoff_accept → AI側のツールが「つながった」を受け取る
HANDOFF: dict[str, dict] = {}
# 呼び出しを鳴らし続ける上限。⚠これを過ぎたらAIは「つかまりませんでした」と伝える。
# 相手を無音で待たせる時間なので長くしすぎない
HANDOFF_TIMEOUT = float(os.environ.get("HANDOFF_TIMEOUT") or "25")


def _prune() -> None:
    now = time.time()
    for k in [k for k, v in RINGING.items() if now - v["since"] > 60]:
        del RINGING[k]
    for k in [
        k for k, v in HANDOFF.items() if now - v["since"] > HANDOFF_TIMEOUT + 30
    ]:
        del HANDOFF[k]


async def prefetch(req: web.Request) -> web.Response:
    number = req.query.get("number", "")
    if not re.fullmatch(r"[+0-9]{4,20}", number):
        # 非通知 (anonymous 等) は下調べ対象外。呼の処理には影響させない
        log.info("prefetch skipped (number=%r)", number)
        return web.json_response({"ok": False, "error": "bad number"}, status=400)
    pool = req.app["pool"]
    await pool.execute(
        "INSERT INTO agent_jobs (kind, query) VALUES ('caller_context', $1)",
        f"caller_context:{number}",
    )
    # 番号のweb検索も同時に起動 (24時間キャッシュ — 同じ番号を毎回調べ直さない)
    cached = await pool.fetchrow(
        """SELECT 1 FROM agent_jobs
           WHERE kind = 'number_lookup' AND query = $1
             AND status IN ('done', 'pending', 'running')
             AND created_at > now() - interval '24 hours' LIMIT 1""",
        number,
    )
    if not cached:
        await pool.execute(
            "INSERT INTO agent_jobs (kind, query) VALUES ('number_lookup', $1)", number
        )
    log.info("prefetch queued: %s (lookup %s)", number, "cached" if cached else "queued")
    return web.json_response({"ok": True})


async def _answer_mode(pool: asyncpg.Pool) -> str:
    """応答モード away / standby / manual (2026-08-01に2状態から3状態化)。

    ⚠正は settings.answer_mode。未設定のときだけ旧 assistant_enabled から導出する。
      移行期の後方互換であって二重管理ではない — 管制室から書くときは
      answer_mode と assistant_enabled の両方を更新する (dashboard の /api/settings)。
    """
    row = await pool.fetchrow("SELECT value FROM settings WHERE key = 'answer_mode'")
    if row and row["value"] in ("away", "standby", "manual"):
        return row["value"]
    row = await pool.fetchrow(
        "SELECT value FROM settings WHERE key = 'assistant_enabled'"
    )
    return "manual" if (row and row["value"] == "false") else "away"


async def answer_mode(req: web.Request) -> web.Response:
    """ダイヤルプランが分岐に使う応答モード。プレーンテキスト。
    hookd停止時はCURLが空を返し、ダイヤルプラン側は away 扱い (fail-open = 従来どおりAIが出る)。"""
    return web.Response(text=await _answer_mode(req.app["pool"]))


async def assistant(req: web.Request) -> web.Response:
    """留守電トグルの状態を返す (管制室のバナー表示など、3状態を要らない側の互換口)。
    プレーンテキスト on/off。standby も「AIが出る」側なので on を返す。
    ⚠ダイヤルプランはこちらではなく /answer_mode を見る (2026-08-01以降)。"""
    mode = await _answer_mode(req.app["pool"])
    return web.Response(text="off" if mode == "manual" else "on")


# 応答モードごとの「端末を鳴らす秒数」。⚠不在(away)は鳴らさない —
# 2026-07-30のワンギリ対策 (ノータイム応答で呼を成立させ発信者に課金させる) を壊さないため。
# ⚠standby を 2.5秒より伸ばすとワンギリが抜けはじめる。伸ばすなら対策の作り直しとセット
STANDBY_RING_SEC = float(os.environ.get("STANDBY_RING_SEC") or "2")
MANUAL_RING_SEC = float(os.environ.get("MANUAL_RING_SEC") or "42")
_RING_SEC = {"away": 0.0, "standby": STANDBY_RING_SEC, "manual": MANUAL_RING_SEC}

# 録音告知の段 (2026-09-25)。着信するとダイヤルプランはまず電話を取って「この通話は録音されます」を
# 流し、その間に下調べと番号検索を回す。端末には相手を出すが、**出るボタンと着信音は告知が終わってから**
# (phase: announcing → ringing。ダイヤルプランが /ringing_phase で切り替える)。
# ⚠ANNOUNCE_MAX_SEC は保険。切り替えが来ないまま (ダイヤルプランが古い・途中で落ちた) でも
#   この秒数を過ぎたら ringing 扱いにする — 出るボタンが永久に出ない方が困る
ANNOUNCE_MAX_SEC = 8.0
# シミュレーション着信で告知の長さを模す秒数 (実音声「この通話は録音されます。」は約2秒)
SIM_ANNOUNCE_SEC = 2.5


def _phase(st: dict) -> str:
    if st.get("phase") == "announcing" and time.time() - st["since"] > ANNOUNCE_MAX_SEC:
        st["phase"] = "ringing"
    return st.get("phase", "ringing")


async def ringing_start(req: web.Request) -> web.Response:
    number = req.query.get("number", "")
    if re.fullmatch(r"[+0-9]{4,20}", number):
        mode = await _answer_mode(req.app["pool"])
        now = time.time()
        # 端末が全部一時停止しているか (2026-09-24)。⚠端末の登録が 1 つも無い構成 (PC だけ) は
        #   対象外 — 従来どおり待つ。管制室の着信バナーから取る余地を残すため
        total, active = await push.listeners(req.app["pool"])
        no_listener = total > 0 and active == 0
        RINGING[number] = {
            "since": now,
            "pickup": False,
            "mode": mode,
            # 端末 (アプリ) がこの時刻まで鳴らす。過ぎたら鳴り止ませる —
            # ダイヤルプランは既に先へ進んでいるので、鳴らし続けても取れない。
            # ⚠告知中は仮の値。/ringing_phase で ringing になった時点から数え直す
            "ring_until": now + (ANNOUNCE_MAX_SEC + _RING_SEC[mode] if _RING_SEC.get(mode) else 0.0),
            "event": asyncio.Event(),
            "no_listener": no_listener,
            "phase": "announcing",
        }
        if no_listener:
            log.info("ringing: %s — 端末が全部一時停止中 (%d台)", number, total)
        log.info("ringing: %s (mode=%s)", number, mode)
        # 端末を鳴らすモードのときだけ FCM で起こす (不在は端末が鳴らないので押さない)。
        # ⚠await しない — ダイヤルプランはこの応答を待ってから鳴らし始める
        if _RING_SEC.get(mode, 0.0) > 0:
            asyncio.create_task(push.wake(req.app["pool"], "ring"))
    return web.Response(text="ok")


async def ringing_phase(req: web.Request) -> web.Response:
    """録音告知が終わった (ダイヤルプランが叩く)。端末の出るボタンと着信音をここから出す。"""
    st = RINGING.get(req.query.get("number", ""))
    if st:
        st["phase"] = "ringing"
        st["ring_until"] = time.time() + _RING_SEC.get(st.get("mode", "away"), 0.0)
        log.info("ringing: %s — 告知終わり、鳴らし始め", req.query.get("number", ""))
    return web.Response(text="ok")


async def pickup(req: web.Request) -> web.Response:
    """Asteriskのダイヤルプランが2秒おきにポーリングする。yes = 受話ボタンが押された。"""
    _prune()
    st = RINGING.get(req.query.get("number", ""))
    return web.Response(text="yes" if st and st["pickup"] else "no")


async def pickup_wait(req: web.Request) -> web.Response:
    """受話をロングポーリングで待つ (2026-08-01、スタンバイの2秒鳴動用)。

    ⚠2秒の猶予を `pickup` の2秒間隔ポーリングで作ると、最悪2秒まるごと取りこぼす
      (押した瞬間には既に次の段へ進んでいる)。押された瞬間に返す口が要る。
    ⚠呼び出し側 (ダイヤルプラン) は CURLOPT(httptimeout) を timeout より長くすること。
      既定の2秒のままだと必ず時間切れになり、受話しても "no" と同じ結果になる。
      (2026-09-25まではダイヤルプランが dltimeout と書いていて Asterisk 20 に無いキーだったため、
      時間切れ自体が効いていなかった。httptimeout に直した)
    """
    _prune()
    number = req.query.get("number", "")
    try:
        timeout = max(0.1, min(float(req.query.get("timeout") or "2"), 60.0))
    except ValueError:
        timeout = 2.0
    st = RINGING.get(number)
    if not st:
        return web.Response(text="no")
    if st["pickup"]:
        return web.Response(text="yes")
    if st.get("decision"):
        # 端末で「AIに任せる」「切る」が押された (告知中に押されていてもここで返る)
        return web.Response(text=st["decision"])
    # ⚠スタンバイで端末が全部一時停止 = 出られる人がいない。数秒待たせずすぐ AI へ (2026-09-24)。
    #   manual / record はここで返さない — 管制室 (PC) の受話ボタンで取る余地が残っている
    if st.get("mode") == "standby" and st.get("no_listener"):
        st["ring_until"] = 0.0
        return web.Response(text="no")
    ev = st.get("event")
    if ev is None:
        ev = st["event"] = asyncio.Event()
    try:
        await asyncio.wait_for(ev.wait(), timeout)
    except asyncio.TimeoutError:
        # ⚠鳴り止ませるのはスタンバイだけ。この時間切れ = ダイヤルプランがAIへ進んだ合図で、
        #   もう本人は取れない。manual は同じ口を細切れに呼び直して鳴らし続けるので、
        #   ここで ring_until を潰すと1回目の時間切れで鳴り止んでしまう
        if st.get("mode") == "standby":
            st["ring_until"] = 0.0
        return web.Response(text="no")
    if st["pickup"]:
        return web.Response(text="yes")
    return web.Response(text=st.get("decision") or "no")


# 受話ボタンで本人が取った番号 → 時刻 (2026-09-25)。agent が通話の頭に1回だけ問い合わせて
# 書記モード (AI は喋らない) かどうかを決める (/picked_up)。
# ⚠以前は agent が留守電の設定 (assistant_enabled) で決めていたので、スタンバイで本人が取ると
#   AI も喋り出し、記録は ai_then_human になっていた。「本人が取ったか」は受話ボタンが知っている
# ⚠RINGING はシミュレーション着信だとブリッジ直後に消えるので、別に持つ
PICKED: dict[str, float] = {}
PICKED_TTL = 90.0


async def picked_up(req: web.Request) -> web.Response:
    """この番号の通話は本人が受話ボタンで取ったか。⚠読んだら消す (同じ番号の次の着信に持ち越さない)"""
    number = req.query.get("number", "")
    t = PICKED.pop(number, None)
    return web.Response(text="yes" if t and time.time() - t < PICKED_TTL else "no")


async def pickup_request(req: web.Request) -> web.Response:
    """受話ボタン (管制室のブラウザ / 専用電話アプリ 共通)。"""
    _prune()
    number = req.query.get("number", "")
    st = RINGING.get(number)
    if not st:
        return web.json_response({"ok": False, "error": "not ringing"}, status=404)
    if _phase(st) == "announcing":
        # 出るボタンはまだ出ていないはず。古い端末・管制室からの早押しは受けない
        return web.json_response({"ok": False, "error": "announcing"}, status=409)
    st["pickup"] = True
    PICKED[number] = time.time()
    ev = st.get("event")
    if ev is not None:
        ev.set()
    log.info("pickup requested: %s", number)
    return web.json_response({"ok": True})


async def ringing_decide(req: web.Request) -> web.Response:
    """着信への答え (2026-09-25)。出る (/pickup_request) 以外の2つ:
      ai     … 残りの呼び出しを待たずに AI に渡す。「自分で出る」モードでもこの1本だけ AI が出る
      reject … 相手ごと切る。AI にも回さない (ユーザー「切るは相手ごと切っていい」)
    ダイヤルプランは /pickup_wait の返事でこれを受け取る。⚠告知中でも受け付ける —
    告知が終わった瞬間に反映される (出るだけは告知の後)"""
    _prune()
    number = req.query.get("number", "")
    action = req.query.get("action", "")
    if action not in ("ai", "reject"):
        return web.json_response({"ok": False, "error": "bad action"}, status=400)
    st = RINGING.get(number)
    if not st:
        return web.json_response({"ok": False, "error": "not ringing"}, status=404)
    st["decision"] = action
    st["ring_until"] = 0.0  # 端末はもう鳴らさない
    ev = st.get("event")
    if ev is not None:
        ev.set()
    log.info("ringing decided: %s → %s", number, action)
    return web.json_response({"ok": True})


async def ringing_state(req: web.Request) -> web.Response:
    """管制室が着信バナー表示のためにポーリングする。"""
    _prune()
    if not RINGING:
        return web.json_response({"ringing": None})
    number, st = max(RINGING.items(), key=lambda kv: kv[1]["since"])
    return web.json_response(
        {
            "ringing": {
                "number": number,
                "since": st["since"],
                "pickup": st["pickup"],
                # 端末を鳴らす判断材料 (2026-08-01)。⚠ブラウザは無視してよい —
                # 管制室のバナーは「鳴っている間ずっと」出す方が親切なので従来どおり
                "mode": st.get("mode", "away"),
                "ring_ms": max(0, int((st.get("ring_until", 0.0) - time.time()) * 1000)),
                # announcing = 録音告知中。相手は出すが、出るボタンと着信音はまだ
                "phase": _phase(st),
            }
        }
    )


async def handoff_request(req: web.Request) -> web.Response:
    """AIが本人を呼び出す (取り次ぎ要求の登録)。agent の request_handoff ツールから。

    ⚠冪等 (2026-08-01)。将来はアシスタントのツールと判断層のORで**同じルームに
      二重に叩かれる前提** (取り次ぎ漏れは致命的・空振りは安いので多めに発火する設計)。
      素朴に上書きすると `since` がリセットされ、呼び出しが HANDOFF_TIMEOUT を超えて
      延びて相手を無音で待たせ続ける — 未承諾の要求が生きている間は合流だけする。"""
    _prune()
    room = req.query.get("room", "")
    if not room:
        return web.json_response({"ok": False, "error": "room is required"}, status=400)
    existing = HANDOFF.get(room)
    if (
        existing
        and not existing["accepted"]
        and time.time() - existing["since"] <= HANDOFF_TIMEOUT
    ):
        log.info("handoff already ringing (joined): %s", room)
        return web.json_response({"ok": True, "timeout": HANDOFF_TIMEOUT, "joined": True})
    # ⚠**一度応答された呼び出しは、同じ通話では鳴らし直さない** (2026-08-01に実機で発覚)。
    #   本人が出て話しているのに、判断層が「相手はまだ本人と話したがっている」と
    #   判定し続けて再発火し、通話中の端末を鳴らし続けていた。
    #   ここはサーバー側の最後の砦 — 発火元が増えても効く (worker側にも同じ趣旨の門番がある)
    if existing and existing["accepted"]:
        log.info("handoff already accepted — 鳴らし直さない: %s", room)
        return web.json_response({"ok": True, "already_accepted": True})
    HANDOFF[room] = {
        "since": time.time(),
        "number": req.query.get("number", ""),
        "reason": req.query.get("reason", "")[:200],
        "accepted": False,
    }
    log.info("handoff requested: %s", room)
    asyncio.create_task(push.wake(req.app["pool"], "handoff"))
    return web.json_response({"ok": True, "timeout": HANDOFF_TIMEOUT})


async def handoff_state(req: web.Request) -> web.Response:
    """受け手 (管制室・将来はアプリ) が「今呼ばれているか」を見張る。"""
    _prune()
    now = time.time()
    # 期限内で未承諾のものだけを「鳴らすべき」として返す
    live = {
        r: v
        for r, v in HANDOFF.items()
        if not v["accepted"] and not v.get("declined") and now - v["since"] <= HANDOFF_TIMEOUT
    }
    if not live:
        return web.json_response({"handoff": None})
    room, st = max(live.items(), key=lambda kv: kv[1]["since"])
    return web.json_response(
        {
            "handoff": {
                "room": room,
                "number": st["number"],
                "reason": st["reason"],
                "since": st["since"],
                "expires_in": HANDOFF_TIMEOUT - (now - st["since"]),
            }
        }
    )


async def handoff_accept(req: web.Request) -> web.Response:
    """受け手が応答した。AI側のツールがこれを見て「つながった」を返す。"""
    _prune()
    st = HANDOFF.get(req.query.get("room", ""))
    if not st:
        return web.json_response({"ok": False, "error": "not ringing"}, status=404)
    st["accepted"] = True
    log.info("handoff accepted: %s", req.query.get("room", ""))
    return web.json_response({"ok": True})


async def handoff_decline(req: web.Request) -> web.Response:
    """受け手が「AIに続けさせる」を押した (2026-09-25)。

    ⚠以前は端末の中だけで鳴り止み、サーバーは HANDOFF_TIMEOUT (25秒) まで待っていた =
      相手はその間ずっと待たされていた。押した時点で AI に「つかまらなかった」と返す"""
    _prune()
    st = HANDOFF.get(req.query.get("room", ""))
    if not st:
        return web.json_response({"ok": False, "error": "not ringing"}, status=404)
    st["declined"] = True
    log.info("handoff declined: %s", req.query.get("room", ""))
    return web.json_response({"ok": True})


async def handoff_result(req: web.Request) -> web.Response:
    """AI側のツールがポーリングする。accepted / waiting / timeout のいずれか。"""
    st = HANDOFF.get(req.query.get("room", ""))
    if not st:
        return web.Response(text="timeout")
    if st["accepted"]:
        return web.Response(text="accepted")
    # 断られた = 時間切れと同じ扱い (AI は「つかまりませんでした」と伝えて用件を預かる)
    if st.get("declined") or time.time() - st["since"] > HANDOFF_TIMEOUT:
        return web.Response(text="timeout")
    return web.Response(text="waiting")


async def screen(req: web.Request) -> web.Response:
    """番号スクリーニング。web検索の判定が sales/scam なら reject を返し、
    Asterisk はAIにつなぐ前に切る (自動ミュート)。判定不明・未完了は ok (fail-open)。"""
    number = req.query.get("number", "")
    row = await req.app["pool"].fetchrow(
        """SELECT result FROM agent_jobs
           WHERE kind = 'number_lookup' AND query = $1 AND status = 'done'
           ORDER BY id DESC LIMIT 1""",
        number,
    )
    if row and row["result"]:
        try:
            verdict = json.loads(row["result"]).get("verdict")
            if verdict in ("sales", "scam"):
                log.info("screen: reject %s (%s)", number, verdict)
                return web.Response(text="reject")
        except (json.JSONDecodeError, AttributeError):
            pass
    return web.Response(text="ok")


async def guard(req: web.Request) -> web.Response:
    """SIP不正利用の関門 (2026-07-26のトールフラウド事故を受けて新設)。

    Asterisk が呼び出し音を鳴らす**前**に叩く — ここで reject を返した呼は
    Ringing も下調べ (Gemini) もAI応答も一切走らない。判定ロジックは security.py。
    hookd が落ちている時は CURL が空を返して通常どおりつながる (fail-open) — 警備の
    仕組みが着信を止めてしまう方が損害が大きいため。"""
    caller = req.query.get("from", "")
    callee = req.query.get("to", "")
    try:
        verdict = await security.evaluate(req.app["pool"], caller, callee, source="hookd")
    except Exception:
        log.exception("guard failed — 通話は通す (fail-open)")
        return web.Response(text="ok")
    return web.Response(text="reject" if verdict.reject else "ok")


async def _prefetch_jobs(pool: asyncpg.Pool, number: str) -> None:
    await pool.execute(
        "INSERT INTO agent_jobs (kind, query) VALUES ('caller_context', $1)",
        f"caller_context:{number}",
    )
    cached = await pool.fetchrow(
        """SELECT 1 FROM agent_jobs
           WHERE kind = 'number_lookup' AND query = $1
             AND status IN ('done', 'pending', 'running')
             AND created_at > now() - interval '24 hours' LIMIT 1""",
        number,
    )
    if not cached:
        await pool.execute(
            "INSERT INTO agent_jobs (kind, query) VALUES ('number_lookup', $1)", number
        )


async def _screen_verdict(pool: asyncpg.Pool, number: str) -> str | None:
    row = await pool.fetchrow(
        """SELECT result FROM agent_jobs
           WHERE kind = 'number_lookup' AND query = $1 AND status = 'done'
           ORDER BY id DESC LIMIT 1""",
        number,
    )
    if row and row["result"]:
        try:
            return json.loads(row["result"]).get("verdict")
        except (json.JSONDecodeError, AttributeError):
            pass
    return None


async def _run_sim_call(pool: asyncpg.Pool, scenario: str, number: str = "") -> None:
    """Asteriskのダイヤルプランを模したテスト着信: 鳴らす→下調べ→(受話 or 9秒)→ブリッジ。
    実着信と同じ管制室体験 (バナー・コール音・受話ボタン・スクリーニング) を再現する。

    ⚠number を指定できるようにした理由 (2026-07-30):
      既定の SIM_NUMBER は電話帳mdに「佐藤 テスト用ダミー」として載っているため、
      AIが下調べで名前を知ってしまい**聞かずに「佐藤様ですね」と言えてしまう**。
      テストが実際より甘くなるので、自動テストは毎回違う番号を渡す。"""
    number = number or SIM_NUMBER
    mode = await _answer_mode(pool)
    now = time.time()
    RINGING[number] = {
        "since": now,
        "pickup": False,
        "mode": mode,
        "ring_until": now + (ANNOUNCE_MAX_SEC + _RING_SEC[mode] if _RING_SEC.get(mode) else 0.0),
        "event": asyncio.Event(),
        "phase": "announcing",
    }
    log.info("sim call: ringing %s (mode=%s)", number, mode)
    await _prefetch_jobs(pool, number)
    # 録音告知の段を模す (実回線はダイヤルプランが告知を流してから /ringing_phase を叩く)
    await asyncio.sleep(SIM_ANNOUNCE_SEC)
    RINGING[number]["phase"] = "ringing"
    RINGING[number]["ring_until"] = time.time() + _RING_SEC.get(mode, 0.0)

    try:
        if mode == "manual":
            # 留守電OFF = 本人が出る。受話ボタン待ち (約42秒)
            for _ in range(21):
                await asyncio.sleep(2)
                st = RINGING.get(number)
                if st and (st["pickup"] or st.get("decision")):
                    break
            else:
                log.info("sim call: 誰も出なかった (不在)")
                return
        else:
            if mode == "standby":
                # ⚠ダイヤルプランと揃えること。スタンバイだけ本人に猶予を与える
                st = RINGING[number]
                try:
                    await asyncio.wait_for(st["event"].wait(), STANDBY_RING_SEC)
                except asyncio.TimeoutError:
                    st["ring_until"] = 0.0
                if st["pickup"]:
                    log.info("sim call: スタンバイ中に本人が出た")
            # ⚠away は待たない。2026-07-30に実経路が「ノータイムで出る」に変わった
            # (ワンギリで折り返させる手合いへの対策)。ズレるとテスト結果が実回線を表さない
            if (await _screen_verdict(pool, number)) in ("sales", "scam"):
                log.info("sim call: screened — 自動拒否")
                return
        # 端末での答え (ダイヤルプランの /pickup_wait と揃える)
        if RINGING.get(number, {}).get("decision") == "reject":
            log.info("sim call: 端末で「切る」— 相手ごと切った")
            return
        # ブリッジ = ルーム作成 + caller-sim を明示ディスパッチ (電話アシスタントは自動参加)
        room = f"call_{number}_sim{int(time.time()) % 1000000}"
        lk = lk_api.LiveKitAPI(
            url=os.environ.get("LIVEKIT_URL", "ws://livekit:7880"),
            api_key=os.environ.get("LIVEKIT_API_KEY", ""),
            api_secret=os.environ.get("LIVEKIT_API_SECRET", ""),
        )
        try:
            await lk.room.create_room(lk_api.CreateRoomRequest(name=room, empty_timeout=120))
            await lk.agent_dispatch.create_dispatch(
                lk_api.CreateAgentDispatchRequest(
                    agent_name="caller-sim", room=room, metadata=scenario
                )
            )
        finally:
            await lk.aclose()
        log.info("sim call: bridged %s", room)
    except Exception:
        log.exception("sim call failed")
    finally:
        RINGING.pop(number, None)


async def simulate_call(req: web.Request) -> web.Response:
    """テスト通話の開始 (管制室の🧪ボタン → dashboard経由で叩かれる)。"""
    scenario = req.query.get("scenario", "").strip()
    # number 省略時は電話帳に載っているダミー番号 (管制室の🧪ボタンはこちら)。
    # 自動テストは毎回違う番号を渡す — 理由は _run_sim_call の注記
    number = re.sub(r"[^\d+]", "", req.query.get("number", ""))
    if number and not re.fullmatch(r"\+?[0-9]{6,20}", number):
        return web.json_response({"ok": False, "error": "番号の形式が不正です"}, status=400)
    asyncio.create_task(_run_sim_call(req.app["pool"], scenario, number))
    return web.json_response({"ok": True, "number": number or SIM_NUMBER})


# ---- 発信 (2026-07-19): 管制室 → /dial → AMI Originate → 応答で outbound-bridge が
#      LiveKit にブリッジ (CALLERID=相手番号 → ルーム名 call_<相手番号>_xxx で既存機構が動く) ----

DIALING: dict[str, dict] = {}  # 番号 → {status: dialing/answered/failed, since, reason}

_ORIGINATE_REASONS = {
    "5": "話し中でした",
    "3": "応答がありませんでした",
    "8": "回線が混み合っています",
    "0": "接続できませんでした",
}


def _prune_dialing() -> None:
    now = time.time()
    for k in [k for k, v in DIALING.items() if now - v["since"] > 180]:
        del DIALING[k]
    # originate タスクごと死んだ場合の保険 (通常は _run_originate が結果を書く)
    for v in DIALING.values():
        if v["status"] == "dialing" and now - v["since"] > 90:
            v["status"] = "failed"
            v["reason"] = "タイムアウト"


async def _ami_read_block(reader: asyncio.StreamReader) -> dict:
    """AMI のイベント/レスポンス1ブロック (空行区切りの Key: Value 群) を読む。"""
    block: dict = {}
    while True:
        line = (await reader.readline()).decode(errors="replace").strip()
        if not line:
            if block:
                return block
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            block[k.strip()] = v.strip()


async def _ami_originate(number: str) -> tuple[bool, str]:
    """AMI で Originate し、相手の応答/失敗 (OriginateResponse) まで待つ。"""
    host = os.environ.get("AMI_HOST", "host.docker.internal")
    port = int(os.environ.get("AMI_PORT", "5038"))
    user = os.environ.get("AMI_USER", "callagent")
    secret = os.environ.get("AMI_SECRET", "callagent-local")
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 5)
    try:
        await reader.readline()  # バナー行 (Asterisk Call Manager/x.y.z)

        async def send(lines: dict) -> None:
            writer.write(
                ("".join(f"{k}: {v}\r\n" for k, v in lines.items()) + "\r\n").encode()
            )
            await writer.drain()

        async def wait_for(action_id: str, timeout: float) -> dict:
            deadline = time.time() + timeout
            while True:
                remain = deadline - time.time()
                if remain <= 0:
                    raise TimeoutError
                blk = await asyncio.wait_for(_ami_read_block(reader), remain)
                if blk.get("ActionID") == action_id:
                    return blk

        await send({"Action": "Login", "ActionID": "login-1", "Username": user, "Secret": secret})
        blk = await wait_for("login-1", 5)
        if blk.get("Response") != "Success":
            return False, f"AMIログイン失敗: {blk.get('Message', '')}"

        action_id = f"dial-{int(time.time() * 1000)}"
        # CallerID必須: 未指定だとAsteriskがFromに "Anonymous" を載せ、ブラステルが
        # 非通知として発信する (相手の非通知ブロックに弾かれる — 2026-07-19実測)
        cid = os.environ.get("OUTBOUND_CALLERID", "")
        await send(
            {
                "Action": "Originate",
                "ActionID": action_id,
                "Channel": f"PJSIP/{number}@brastel",
                "Context": "outbound-bridge",
                "Exten": "s",
                "Priority": "1",
                "CallerID": f'"{cid}" <{cid}>',
                "Variable": f"OUTNUM={number}",
                "Async": "true",
                "Timeout": "40000",  # 呼び出し40秒
            }
        )
        # Async: true → 即時の Response (queued) と、応答/失敗時の OriginateResponse イベント
        while True:
            blk = await wait_for(action_id, 55)
            if blk.get("Event") == "OriginateResponse":
                if blk.get("Response") == "Success":
                    return True, ""
                reason = blk.get("Reason", "")
                return False, _ORIGINATE_REASONS.get(
                    reason, f"接続できませんでした (reason={reason})"
                )
            if blk.get("Response") == "Error":
                return False, blk.get("Message", "Originateエラー")
    except TimeoutError:
        return False, "タイムアウト"
    finally:
        writer.close()


async def _run_originate(number: str) -> None:
    st = DIALING[number]
    try:
        ok, reason = await _ami_originate(number)
        if ok:
            st["status"] = "answered"  # ダイヤルプランの /outbound_answered とどちらが先でも冪等
            log.info("dial answered: %s", number)
        else:
            st["status"] = "failed"
            st["reason"] = reason
            log.info("dial failed: %s (%s)", number, reason)
    except Exception as e:
        log.exception("originate failed")
        st["status"] = "failed"
        st["reason"] = f"AMI接続エラー: {e}"


async def dial(req: web.Request) -> web.Response:
    """管制室からの発信。ブラステル経由の実発信 = 通話料がかかる。"""
    _prune_dialing()
    number = re.sub(r"[-\s()]", "", req.query.get("number", ""))
    if not re.fullmatch(r"\+?[0-9]{4,20}", number):
        return web.json_response({"ok": False, "error": "番号の形式が不正です"}, status=400)
    if number == SIM_NUMBER:
        return web.json_response(
            {"ok": False, "error": "テスト用ダミー番号には発信できません"}, status=400
        )
    st = DIALING.get(number)
    if st and st["status"] == "dialing":
        return web.json_response({"ok": False, "error": "この番号に発信中です"}, status=409)
    pool = req.app["pool"]
    # 発信の印: agent がこれを見て書記モード (本人が話す・AIは黙って支援) で入室する。
    # status='done' で入れる — worker のジョブ回収対象にしない
    await pool.execute(
        """INSERT INTO agent_jobs (kind, query, status, result, finished_at)
           VALUES ('outbound_call', $1, 'done', 'dial', now())""",
        number,
    )
    await _prefetch_jobs(pool, number)  # 発信でも下調べ (電話帳+過去通話+番号検索) を回す
    DIALING[number] = {"status": "dialing", "since": time.time(), "reason": ""}
    asyncio.create_task(_run_originate(number))
    log.info("dial: %s", number)
    return web.json_response({"ok": True, "number": number})


async def outbound_answered(req: web.Request) -> web.Response:
    """Asterisk の outbound-bridge が相手の応答直後に叩く。"""
    number = req.query.get("number", "")
    st = DIALING.get(number)
    if st:
        st["status"] = "answered"
    log.info("outbound answered: %s", number)
    return web.Response(text="ok")


async def call_ended(req: web.Request) -> web.Response:
    """Asterisk のダイヤルプラン h (hangup) が叩く。着信/発信バナーを即座に畳む。

    ⚠ これが無いと「終わった通話のバナーが出っぱなし」になる (2026-07-30 に指摘を受けて追加)。
    _prune_dialing / _prune は 180秒 / 60秒の保険であって、実態の反映ではなかった —
    通話が切れたことを誰も hookd に伝えていなかったので、管制室は最大3分間
    「発信中・接続中…」を表示し続けていた。ポーリング間隔 (1.5秒) の問題ではない。
    """
    number = req.query.get("number", "")
    gone = [d.pop(number, None) is not None for d in (RINGING, DIALING)]
    log.info("call ended: %s (ringing=%s dialing=%s)", number, *gone)
    return web.Response(text="ok")


async def dial_state(req: web.Request) -> web.Response:
    """管制室が発信バナー表示のためにポーリングする。"""
    _prune_dialing()
    if not DIALING:
        return web.json_response({"dialing": None})
    number, st = max(DIALING.items(), key=lambda kv: kv[1]["since"])
    return web.json_response(
        {
            "dialing": {
                "number": number,
                "status": st["status"],
                "since": st["since"],
                "reason": st["reason"],
            }
        }
    )


async def _reaper(app: web.Application) -> None:
    """孤児通話の看取り係。agentが異常終了すると ended_at が打たれず「通話中」の行が
    永久に残る (サイドバーに通話中が複数並ぶ・プロバイダが死んだルームに接続する原因)。
    LiveKitの実ルーム一覧と突き合わせ、ルームが消えた通話行を自動クローズする。"""
    pool = app["pool"]
    while True:
        try:
            lk = lk_api.LiveKitAPI(
                url=os.environ.get("LIVEKIT_URL", "ws://livekit:7880"),
                api_key=os.environ.get("LIVEKIT_API_KEY", ""),
                api_secret=os.environ.get("LIVEKIT_API_SECRET", ""),
            )
            try:
                rooms = await lk.room.list_rooms(lk_api.ListRoomsRequest())
                names = [r.name for r in rooms.rooms]
            finally:
                await lk.aclose()
            result = await pool.execute(
                """UPDATE calls SET ended_at = now()
                   WHERE ended_at IS NULL
                     AND started_at < now() - interval '30 seconds'
                     AND NOT (room_name = ANY($1::text[]))""",
                names,
            )
            if result and not result.endswith(" 0"):
                log.info("reaper: 孤児通話をクローズ (%s)", result)
        except Exception:
            log.exception("reaper failed (継続)")
        await asyncio.sleep(20)


# 通話録音の保存期間 (日)。⚠**0 = 掃除しない (既定)**。
#
# なぜ既定で消さないか (2026-08-01):
#   録音は取り返しがつかない。8kHzモノラルで約1MB/分なので放置すれば必ず溜まるが、
#   何日で消してよいかは運用の判断 (相手への告知内容とも関わる) で、こちらでは決められない。
#   仕組みだけ置いて、日数を入れたときにだけ動く形にしてある。
# ⚠これを有効にする前に、通話記録md (文字起こし) が残ることを確認すること。
#   録音は「文字起こしが取りこぼした数字を後から耳で確認する」ための保険なので、
#   文字起こしまで一緒に消える運用になっていたら保険の意味が無い。
RECORDING_DIR = os.environ.get("RECORDING_DIR", "/recordings")
RECORDING_RETENTION_DAYS = float(os.environ.get("RECORDING_RETENTION_DAYS") or "0")


async def _recording_reaper(app: web.Application) -> None:
    """保存期間を過ぎた録音を消す。RECORDING_RETENTION_DAYS が 0 なら何もしない。"""
    if RECORDING_RETENTION_DAYS <= 0:
        log.info("録音の掃除は無効 (RECORDING_RETENTION_DAYS 未設定)")
        return
    from pathlib import Path

    cutoff_sec = RECORDING_RETENTION_DAYS * 86400
    while True:
        try:
            now = time.time()
            gone = bytes_freed = 0
            for p in Path(RECORDING_DIR).glob("*.wav"):
                try:
                    st = p.stat()
                    if now - st.st_mtime <= cutoff_sec:
                        continue
                    p.unlink()
                    gone += 1
                    bytes_freed += st.st_size
                except OSError:
                    # 録音中のファイル等。次の周回で拾えばよい
                    continue
            if gone:
                log.info(
                    "録音を掃除: %d件 %.1fMB (保存期間 %g日)",
                    gone, bytes_freed / 1e6, RECORDING_RETENTION_DAYS,
                )
        except Exception:
            log.exception("recording reaper failed (継続)")
        await asyncio.sleep(3600)


async def _security_notifier(app: web.Application) -> None:
    """検知イベントの通知係。攻撃中は1件ずつ通知すると100通になるので、
    15秒ごとに未通知分をまとめて1通にして Webhook (Discord/Slack) へ送る。
    継続中の攻撃は security.notify_pending 側で10分おきに再通知される。
    SECURITY_WEBHOOK_URL 未設定なら通知は行わない (管制室UIには出る)。"""
    pool = app["pool"]
    ticks = 0
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                await security.notify_pending(pool, session)
                ticks += 1
                if ticks % 240 == 0:  # 1時間ごとに試行ログを掃除
                    await security.prune(pool)
            except Exception:
                log.exception("security notifier failed (継続)")
            await asyncio.sleep(15)


async def make_app() -> web.Application:
    app = web.Application()
    app["pool"] = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
    # 警備テーブルは init.sql (初回initdbのみ) ではなくここで冪等に作る — 既に動いているDBにも入る
    await security.ensure_schema(app["pool"])
    try:
        await app["pool"].execute(push.DDL)  # 端末のプッシュトークン表 (2026-09-18)
    except Exception:
        log.debug("push DDL", exc_info=True)
    # ⚠FCM の鍵は起動時に別スレッドで読んでおく (2026-09-24)。鍵の読み込み (RSA の解析) は同期処理で、
    #   最初の着信のときに初めて読むとイベントループが約 0.9 秒止まり、スタンバイの鳴動秒数と
    #   /pickup_wait の返りがずれた (test_answer_mode の「8秒で返る」が 8.93 秒で FAIL)
    await asyncio.to_thread(push.preload)

    async def _start_reaper(app: web.Application) -> None:
        app["reaper"] = asyncio.create_task(_reaper(app))
        app["notifier"] = asyncio.create_task(_security_notifier(app))
        app["rec_reaper"] = asyncio.create_task(_recording_reaper(app))

    app.on_startup.append(_start_reaper)
    app.router.add_get("/prefetch", prefetch)
    app.router.add_get("/assistant", assistant)
    app.router.add_get("/answer_mode", answer_mode)
    app.router.add_get("/ringing_start", ringing_start)
    app.router.add_get("/ringing_phase", ringing_phase)
    app.router.add_get("/ringing_decide", ringing_decide)
    app.router.add_get("/picked_up", picked_up)
    app.router.add_get("/handoff_decline", handoff_decline)
    app.router.add_get("/pickup", pickup)
    app.router.add_get("/pickup_wait", pickup_wait)
    app.router.add_get("/pickup_request", pickup_request)
    app.router.add_get("/ringing_state", ringing_state)
    app.router.add_get("/handoff_request", handoff_request)
    app.router.add_get("/handoff_state", handoff_state)
    app.router.add_get("/handoff_accept", handoff_accept)
    app.router.add_get("/handoff_result", handoff_result)
    app.router.add_get("/screen", screen)
    app.router.add_get("/guard", guard)
    app.router.add_get("/simulate_call", simulate_call)
    app.router.add_get("/dial", dial)
    app.router.add_get("/outbound_answered", outbound_answered)
    app.router.add_get("/dial_state", dial_state)
    app.router.add_get("/call_ended", call_ended)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    web.run_app(make_app(), host="0.0.0.0", port=8790)
