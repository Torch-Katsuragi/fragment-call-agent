# -*- coding: utf-8 -*-
"""通話への参加 (待機・視聴・会話) と保留 (2026-09-26)。

形: 端末 (アプリ・管制室) はそれぞれ 3 つの状態を自由に行き来する。
  待機 … ルームに入らない。状態 (/api/device/state) だけ見ている
  視聴 … 聞くだけで入る (identity `watch-<端末id>`)。文字起こしが見え、音は任意
  会話 … 本人として入る (identity `operator-<端末id>`)。マイクもスピーカーもオン
通話の担い手は端末の状態から決まる (agent.py の _apply_handler):
  会話中の端末が 1 台以上           → human (AI は書記だけ)
  0 台で AI 応答がオン (calls.ai_on) → ai
  0 台で AI 応答がオフ              → hold (AI が保留音を流す)
⚠「保留」は押す操作ではなく状態。会話中の人が抜ければ保留になり、誰かが会話に入れば解ける。
  保留転送はこれだけで成り立つ (A が抜ける → B を呼ぶ → B が入る)。

担い手と参加者は agent が calls に書く (handler / presence / hold_since)。端末は DB を読むだけ。
AI 応答のオン・オフ (ai_on) は端末が書き、agent_push で agent に知らせる。

保留中に「誰を呼ぶか」の候補はメンバー (名前と担当 duty)。rank が会話の流れから並べ替える。
⚠端末は名前も役割も持たない (2026-10-04)。端末の情報はログイン (device_sessions) の行だけ。
"""

import json
import logging
import os

import asyncio

import aiohttp

import members
import vertex

log = logging.getLogger("presence")

# ⚠init.sql にも同じものがある (初回 initdb 用)。稼働中の DB へは hookd の起動時にこれを流す
DDL = """
ALTER TABLE calls ADD COLUMN IF NOT EXISTS ai_on boolean;
ALTER TABLE calls ADD COLUMN IF NOT EXISTS handler text;
ALTER TABLE calls ADD COLUMN IF NOT EXISTS hold_since timestamptz;
ALTER TABLE calls ADD COLUMN IF NOT EXISTS presence jsonb NOT NULL DEFAULT '[]'::jsonb;
-- 通話ごとの相手の呼び名 (2026-09-26)。同じ番号でも話した人は通話ごとに違う (組織の番号)。
-- NULL = 電話帳の名前のままでよい。worker が終話後に書く
ALTER TABLE calls ADD COLUMN IF NOT EXISTS caller_label text;
-- 録音 (2026-10-02)。ワークスペースからの相対パス (録音/YYYY-MM/....ogg)。NULL = 無い・消した。
-- worker (recordings.py) が書き、管制室が再生に使う
ALTER TABLE calls ADD COLUMN IF NOT EXISTS recording_path text;
-- 通話で会話した人の名前 (2026-10-04)。agent が担い手の変わり目ごとに足す。終話後も残る
ALTER TABLE calls ADD COLUMN IF NOT EXISTS handled_by jsonb NOT NULL DEFAULT '[]'::jsonb;
"""

# 呼び出し候補に出す端末。しばらく見かけない端末 (機種変更で使わなくなった等) は出さない
CANDIDATE_DAYS = 14

RANK_PROMPT = """\
電話の保留中に、次に電話に出てもらう人を選ぶ手伝いをする。
候補はメンバーの名前と担当。直前までの会話から、用件に合う順に並べる。
担当が空の人や用件と関係が読めない人は後ろにする。
出力は JSON だけ: {"order": [候補の番号, ...], "reasons": {"候補の番号": "20字以内の理由"}}
理由は用件と担当が結びつく人にだけ付ける。"""


async def candidates(pool, exclude: str = "") -> list[dict]:
    """呼び出し候補 (最近見かけた順)。exclude = 呼ぶ側の端末 id。
    ⚠候補は人 (メンバー) 単位 (2026-10-04)。端末は名前を持たず、同じアカウントを何台に入れても 1 人として出す。
      id はその人の端末 id をカンマでつないだもの = 選ぶと全部鳴る (hookd の targets はカンマ区切り)"""
    rows = await pool.fetch(
        f"""SELECT m.id AS member_id, coalesce(nullif(m.name, ''), split_part(m.email, '@', 1)) AS name,
                   m.duty, string_agg(DISTINCT s.device_id, ',') AS ids
            FROM {members.ACTIVE_SESSIONS}
            WHERE s.device_id <> $1 AND s.last_seen > now() - interval '{CANDIDATE_DAYS} days'
            GROUP BY m.id
            ORDER BY max(s.last_seen) DESC""",
        exclude,
    )
    return [{"id": r["ids"], "name": r["name"], "role": r["duty"], "reason": ""} for r in rows]


async def rank(cands: list[dict], convo: list[tuple[str, str]]) -> list[dict]:
    """会話の流れから候補を並べ替える。失敗・時間切れなら元の順 (最近見かけた順) のまま返す。

    ⚠並べ替えは補助。呼ぶ相手は本人が選ぶので、ここが外れても実害は「探す手間」だけ。
      だから待たせない (4 秒で諦める)
    """
    if len(cands) < 2 or not vertex.available() or not convo:
        return cands
    model = os.environ.get("RANK_MODEL", "gemini-3.8-flash")
    lines = "\n".join(f"{i}. {c['name']}（担当: {c['role'] or 'なし'}）" for i, c in enumerate(cands))
    talk = "\n".join(f"{'相手' if s == 'caller' else '応対側'}: {t}" for s, t in convo[-20:])
    body = {
        "systemInstruction": {"parts": [{"text": RANK_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": f"# 候補\n{lines}\n\n# 会話\n{talk}"}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingLevel": "low"},
        },
    }
    try:
        # 呼び先は Vertex AI か AI Studio (vertex.py)。⚠トークンの取得は同期なので別スレッドで
        headers = await asyncio.to_thread(vertex.headers)
        url = vertex.url(f"models/{model}:generateContent")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as s:
            async with s.post(url, headers=headers, json=body) as r:
                data = await r.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        out = json.loads(text)
        order = [int(i) for i in out.get("order", []) if 0 <= int(i) < len(cands)]
        reasons = {int(k): str(v)[:30] for k, v in (out.get("reasons") or {}).items()}
    except Exception as e:
        log.info("候補の並べ替えに失敗 (元の順で返す): %s", e)
        return cands
    seen = set()
    ranked = []
    for i in order + list(range(len(cands))):
        if i in seen:
            continue
        seen.add(i)
        ranked.append({**cands[i], "reason": reasons.get(i, "")})
    return ranked
