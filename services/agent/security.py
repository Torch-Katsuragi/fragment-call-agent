# -*- coding: utf-8 -*-
"""SIP不正利用 (トールフラウド) の検知・遮断・通知。

出発点は 2026-07-26 に実際に踏んだ事故:
GCP VM公開の直後、SIPスキャナー (66.165.237.98) が公開ポート5060のLiveKit SIPへ直接
INVITEを送り、内線番号の総当たり (from=1001/2000/3001/4000/4001/5000/5001) と
英国プレミアム番号 (to=0009441904911180) への中継を試みた。dispatch ruleがそのまま
ルームを作り**AIが自動応答**したため、100件超の通話でSTT/TTS/Geminiを消費した。
FW (GCP明示DENY + VM側iptables) と trunk の allowed_addresses で遮断はしたが、
**気づいたのは人間が通話履歴を目視したから**で、検知の仕組みが無かった。

そこで多層で入れる (どの層も同じ判定ロジックと同じ記録先 security_events を使う):

1. hookd `/guard` — Asteriskが呼び出し音を鳴らす前に叩く関門。通常の着信経路はここで止まる。
   下調べ (caller_context / number_lookup) より前に判定するので、拒否した呼はGemini課金ゼロ
2. agent entrypoint — FW/trunk制限をすり抜けて直接LiveKitに入ってきた呼の最終防衛線。
   AgentSession生成の前に切るので STT/TTS/LLM を1トークンも使わせない
3. 管制室UI + Webhook通知 — 遮断できても「気づけない」のが今回の本質的な失敗だったので、
   拒否はすべて security_events に残し、まとめて通知する

判定の考え方 (誤検知でまともな着信を落とさないための線引き):
- **こちらへの着信が国際番号を宛先にしている = 中継させられている**。正常な着信の宛先は
  必ず自分のDIDなので、この条件は原理的に誤検知しない。トールフラウドの本体はここ
- **発信者番号が内線風 (数桁) / 数字ですらない** のは、日本の回線から来る通常の着信では
  起こらない (非通知は "anonymous" 等の既定マーカーで来るので別扱い)
- レート制限は「同一発信者が短時間に何度も」だけを見る。実在の相手が5分に5回かけ直すことは
  まずないが、それでも恒久ブロックはせず時限 (既定1時間) にしてある

環境変数 (すべて任意 — 既定値のままで上記の事故は止まる):
  SECURITY_ENFORCE=0            拒否せず検知・通知だけ行う (様子見モード)
  SECURITY_RATE_MAX / _WINDOW_SEC / SECURITY_BLOCK_SEC   レート制限と自動ブロックの長さ
  SECURITY_STORM_MAX / _WINDOW_SEC                        攻撃ストームの警報しきい値
  SECURITY_MIN_CALLER_DIGITS    これ未満の桁数の発信者番号は内線総当たりとみなす (既定6)
  SECURITY_LOCAL_NUMBERS        自分のDID (カンマ区切り)。宛先がこれなら国際判定をしない
  SECURITY_ALLOW_NUMBERS        判定を丸ごと免除する番号 (テスト用ダミー等)
  SECURITY_STRICT_CALLEE=1      LOCAL_NUMBERS 以外の宛先をすべて拒否 (既定は国際発信だけ拒否)
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

log = logging.getLogger("security")

SIM_NUMBER = "09012345678"  # テスト通話のダミー番号 (hookd と同じ値)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_numbers(name: str) -> set[str]:
    raw = os.environ.get(name, "").replace(";", ",").replace(" ", "")
    return {normalize(x) for x in raw.split(",") if x.strip()} - {""}


RATE_MAX = _env_int("SECURITY_RATE_MAX", 5)
RATE_WINDOW_SEC = _env_int("SECURITY_RATE_WINDOW_SEC", 600)
BLOCK_SEC = _env_int("SECURITY_BLOCK_SEC", 3600)
STORM_MAX = _env_int("SECURITY_STORM_MAX", 12)
STORM_WINDOW_SEC = _env_int("SECURITY_STORM_WINDOW_SEC", 300)
MIN_CALLER_DIGITS = _env_int("SECURITY_MIN_CALLER_DIGITS", 6)
ATTEMPT_RETENTION_DAYS = _env_int("SECURITY_ATTEMPT_RETENTION_DAYS", 14)
# 同じ呼を hookd と agent の2層で二重に数えないための照合窓 (呼び出し猶予9秒+入室を包む長さ)
ATTEMPT_DEDUP_SEC = _env_int("SECURITY_ATTEMPT_DEDUP_SEC", 60)
ENFORCE = os.environ.get("SECURITY_ENFORCE", "1") != "0"
STRICT_CALLEE = os.environ.get("SECURITY_STRICT_CALLEE", "0") == "1"

# 非通知・番号取得失敗を表す既定マーカー。これらは「不審」ではない (従来どおり通話は通す)
ANON_MARKERS = {
    "", "s", "anonymous", "unknown", "unavailable", "restricted", "private",
    "unassigned", "null", "n/a",
}

# 国際プレミアム番号帯。**拒否の判断はこれではなく「着信なのに宛先が国際番号」の一点**で、
# この表は通知に「どういう手口か」を書き添えるためのラベル付けにだけ使う。
#
# ⚠**この表は当てにならない** (2026-08-01にテストを書いて判明)。
#   7/26の実攻撃 `0009441904911180` は国番号以降が `441904911180` = 英国の 01904 (York) 帯で、
#   下の `^44(9|8)` に**当たらない**。つまり「攻撃と同じ手口」と書いてあるラベルが
#   当の攻撃を取りこぼす。プレミアム帯を狙うとは限らず、収益分配のある地理番号でも成立するため。
#   → 表を継ぎ足して当てにいかない。**拒否は宛先が国際番号という一点で足りている**ので、
#     ここは「たまたま分かれば書き添える」以上の役目を持たせないこと。
PREMIUM_LABELS: list[tuple[str, str]] = [
    (r"^44(9|8)", "英国のプレミアム/サービス番号帯"),
    (r"^1(900|809|876)", "北米・カリブのプレミアム/折返し詐欺で常用される帯"),
    (r"^88[0-5]", "衛星電話帯 (分単位の課金が極端に高い)"),
    (r"^2[3-6][0-9]", "西アフリカ諸国 (トールフラウドの常連宛先)"),
    (r"^(37[0-9]|38[0-9])", "東欧・バルト諸国 (トールフラウドの常連宛先)"),
    (r"^(67[0-9]|68[0-9])", "太平洋諸島 (トールフラウドの常連宛先)"),
]

KIND_LABELS = {
    "premium_relay": "国際プレミアム番号への中継",
    "international_relay": "国際番号への中継",
    "unknown_callee": "自分の番号宛でない着信",
    "extension_scan": "内線番号の総当たり",
    "malformed_caller": "不正な発信者番号",
    "rate_limit": "短時間の連続着信",
    "blocked_repeat": "ブロック中の番号からの再着信",
    "storm": "着信ストーム",
    "intl_domestic_caller": "国内番号が国際表記で着信",
}


@dataclass
class Verdict:
    """判定結果。reject=True なら呼をつながずに切る。"""

    reject: bool = False
    kind: str = ""
    severity: str = "warn"  # info / warn / critical
    detail: str = ""
    action: str = "allow"  # reject / observe (SECURITY_ENFORCE=0時) / allow / alert
    extra: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)


def normalize(number: str | None) -> str:
    """表記揺れを吸収して数字列にする (+81-50-1234-5678 → 05012345678)。
    数字が1桁も無ければ空文字 (= 非通知・不明扱い)。

    ⚠**この畳み込みで「国際表記で来た」という事実が消える**。同一性の判定
      (電話帳・レート制限・ブロック) には畳んだ形が要るので畳むのは正しいが、
      畳んだ結果だけを見ると `+81 90…` と `090…` の区別が付かない。
      その事実が要る側は `came_via_intl()` を使うこと (2026-08-01)。
    """
    if not number:
        return ""
    s = re.sub(r"[^\d+]", "", number).lstrip("+")
    if s.startswith("81") and len(s) >= 11:
        s = "0" + s[2:]
    return s


def came_via_intl(number: str | None) -> bool:
    """発信者番号が**国際表記の日本番号**で届いたか (`+81…` / `0081…`)。

    なぜ見るか (2026-08-01):
      発信者番号は偽装され得る (警察庁も「表示された番号を信用するな」と公表) が、
      **どの経路で届いたか**は騙りにくい。国内の相手からの着信が国際ゲートウェイ経由で
      入ってくるのは筋が通らないので、番号そのものより手掛かりになる。

    ⚠**これで拒否はしない**。実測 (2026-08-01時点の全131件) では `+81` 表記の着信は
      **1件も無い** — ブラステルもTwilioも国内番号は `090…` の形で渡してくる。
      つまり出たら異常だが、母数ゼロで拒否を作ると**最初の1件が誤爆したときに
      正常な着信を落とす**。まずは記録と通知に載せ、実例が出てから判断する。
    """
    if not number:
        return False
    s = re.sub(r"[^\d+]", "", number)
    return s.startswith("+81") or s.startswith("0081")


def _is_anon(raw: str | None) -> bool:
    return (raw or "").strip().lower() in ANON_MARKERS


def _premium_label(intl: str) -> str | None:
    for pattern, label in PREMIUM_LABELS:
        if re.match(pattern, intl):
            return label
    return None


# 国際発信プレフィックスの読み方。3桁 (010 / 00X) と4桁 (00XY = 事業者選択) の両方があり、
# どちらとも読める番号があるので**両方を候補に出す**
_INTL_SPLITS = (r"^(010|00\d)(\d{6,})$", r"^(00\d\d)(\d{6,})$")


def _intl_candidates(callee: str) -> list[str]:
    """宛先から「国番号以降」の候補を切り出す。

    ⚠**一意に決まらない** (2026-08-01にテストを書いて判明)。日本の国際プレフィックスは
      010 と 00XY で桁数が揃っていないので、`0019001234567` は
      「001 + 9001234567 (米国の1-900帯)」とも「0019 + 001234567」とも読める。
      以前は `00\\d{1,2}` の貪欲マッチで後者に決め打っていて、**通知に出る
      「国番号以降 …」が実際と食い違い、プレミアム帯の判定も外れていた**。
      決め打たずに候補を全部返し、ラベルが付くものがあればそれを採る。
    """
    out = []
    for pattern in _INTL_SPLITS:
        m = re.match(pattern, callee)
        if m and m.group(2) not in out:
            out.append(m.group(2))
    return out


def local_numbers() -> set[str]:
    return _env_numbers("SECURITY_LOCAL_NUMBERS")


def allow_numbers() -> set[str]:
    return _env_numbers("SECURITY_ALLOW_NUMBERS") | {SIM_NUMBER}


def classify(caller_raw: str | None, callee_raw: str | None = None) -> Verdict:
    """DBを見ない静的判定 (パターン検知)。単体テストしやすいように純関数にしてある。"""
    callee = normalize(callee_raw)
    locals_ = local_numbers()
    if callee and callee not in locals_:
        # 国際発信プレフィックス: 日本の 010/00XX (事業者選択)、および攻撃で実測した 000/002。
        # 「掛かってきた電話の宛先が国際番号」は正常な着信では起こり得ない = 中継の踏み台狙い
        cands = _intl_candidates(callee)
        if cands:
            # ラベルが付く読み方があればそれを採る。無ければ最初の候補で説明する
            labeled = next(((c, _premium_label(c)) for c in cands if _premium_label(c)), None)
            intl, label = labeled if labeled else (cands[0], None)
            return Verdict(
                reject=True,
                kind="premium_relay" if label else "international_relay",
                severity="critical",
                detail=(
                    f"着信の宛先が国際番号 {callee} (国番号以降 {intl})"
                    + (f" — {label}" if label else "")
                    + "。こちらを踏み台にした国際中継の試み"
                ),
                extra={"callee": callee, "intl_candidates": cands},
            )
        if STRICT_CALLEE and locals_:
            return Verdict(
                reject=True,
                kind="unknown_callee",
                severity="warn",
                detail=f"宛先 {callee} は自分の番号 ({'/'.join(sorted(locals_))}) ではない",
                extra={"callee": callee},
            )

    if _is_anon(caller_raw):
        return Verdict()  # 非通知 — 従来どおり通す (プロンプト側に非通知の応対がある)

    caller = normalize(caller_raw)
    if not caller:
        # 数字が1桁も無いのに非通知マーカーでもない = スキャナのツール名等 (sipvicious 等)
        return Verdict(
            reject=True,
            kind="malformed_caller",
            severity="warn",
            detail=f"発信者番号が数字でない: {str(caller_raw)[:40]!r}",
        )
    if len(caller) < MIN_CALLER_DIGITS:
        return Verdict(
            reject=True,
            kind="extension_scan",
            severity="critical",
            detail=(
                f"発信者番号が {len(caller)} 桁 ({caller}) — 日本の回線からの着信では"
                "起こらない。PBXの内線番号を総当たりするスキャナの特徴"
            ),
        )
    return Verdict()


# ---------------------------------------------------------------- DB (asyncpg)

DDL = [
    # 着信の試行ログ。calls とは別に持つ: 拒否した呼は calls 行を作らないので
    # レート制限・ストーム判定の母数がここにしか無い
    """CREATE TABLE IF NOT EXISTS call_attempts (
         id            bigserial PRIMARY KEY,
         at            timestamptz NOT NULL DEFAULT now(),
         caller_number text,
         callee_number text,
         source        text NOT NULL DEFAULT 'hookd',   -- hookd / agent
         verdict       text NOT NULL DEFAULT 'allow',   -- allow / reject / observe
         kind          text NOT NULL DEFAULT ''
       )""",
    "CREATE INDEX IF NOT EXISTS call_attempts_at_idx ON call_attempts (at DESC)",
    "CREATE INDEX IF NOT EXISTS call_attempts_caller_idx ON call_attempts (caller_number, at DESC)",
    # 検知イベント。同種・同一発信者の連続は1行にまとめる (攻撃1回で100行増えても読めないため)
    """CREATE TABLE IF NOT EXISTS security_events (
         id            bigserial PRIMARY KEY,
         first_at      timestamptz NOT NULL DEFAULT now(),
         last_at       timestamptz NOT NULL DEFAULT now(),
         count         int NOT NULL DEFAULT 1,
         kind          text NOT NULL,
         severity      text NOT NULL DEFAULT 'warn',    -- info / warn / critical
         source        text NOT NULL DEFAULT 'hookd',
         action        text NOT NULL DEFAULT 'reject',  -- reject / observe / alert
         caller_number text,
         callee_number text,
         detail        text NOT NULL DEFAULT '',
         acknowledged  boolean NOT NULL DEFAULT false,
         notified_at   timestamptz
       )""",
    "CREATE INDEX IF NOT EXISTS security_events_last_idx ON security_events (last_at DESC)",
    """CREATE TABLE IF NOT EXISTS blocked_callers (
         number        text PRIMARY KEY,
         reason        text NOT NULL DEFAULT '',
         blocked_until timestamptz,                     -- NULL = 恒久 (管制室から手動追加した分)
         hits          int NOT NULL DEFAULT 0,
         created_at    timestamptz NOT NULL DEFAULT now()
       )""",
]


async def ensure_schema(pool) -> None:
    """スキーマを冪等に作る。init.sql は初回 initdb でしか走らないため、
    既存DB (VM・ローカルとも稼働中) への追加はここで面倒を見る。"""
    for stmt in DDL:
        try:
            await pool.execute(stmt)
        except Exception:
            # 同時起動 (hookd と agent) の競合は IF NOT EXISTS でも稀に上がる — 無視してよい
            log.debug("ensure_schema: %s ...", stmt.split("(")[0].strip(), exc_info=True)


async def _record_attempt(pool, caller: str, callee: str, source: str, verdict: Verdict) -> None:
    await pool.execute(
        """INSERT INTO call_attempts (caller_number, callee_number, source, verdict, kind)
           VALUES ($1, $2, $3, $4, $5)""",
        caller or None,
        callee or None,
        source,
        verdict.action if verdict.reject else "allow",
        verdict.kind,
    )


async def record_event(pool, verdict: Verdict, caller: str, callee: str, source: str) -> int | None:
    """検知イベントを記録する。直近10分の同種・同一発信者イベントがあれば件数を足す
    (通知側は last_at を見て、続いている攻撃を10分おきに再通知する)。"""
    row = await pool.fetchrow(
        """UPDATE security_events SET last_at = now(), count = count + 1,
                  callee_number = COALESCE($4, callee_number), detail = $5
           WHERE id = (SELECT id FROM security_events
                       WHERE kind = $1 AND caller_number IS NOT DISTINCT FROM $2
                         AND source = $3 AND last_at > now() - interval '10 minutes'
                       ORDER BY id DESC LIMIT 1)
           RETURNING id""",
        verdict.kind,
        caller or None,
        source,
        callee or None,
        verdict.detail,
    )
    if row:
        return row["id"]
    row = await pool.fetchrow(
        """INSERT INTO security_events
             (kind, severity, source, action, caller_number, callee_number, detail)
           VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id""",
        verdict.kind,
        verdict.severity,
        source,
        verdict.action,
        caller or None,
        callee or None,
        verdict.detail,
    )
    return row["id"] if row else None


async def is_blocked(pool, caller: str) -> bool:
    if not caller:
        return False
    row = await pool.fetchrow(
        """SELECT number FROM blocked_callers
           WHERE number = $1 AND (blocked_until IS NULL OR blocked_until > now())""",
        caller,
    )
    return row is not None


async def block(pool, caller: str, reason: str, seconds: int | None = None) -> None:
    """時限ブロック。seconds=None は恒久 (管制室からの手動ブロック用)。
    ブロック中に再着信があれば期限を延ばす — 叩き続ける相手ほど長く閉め出す。"""
    if not caller:
        return
    await pool.execute(
        """INSERT INTO blocked_callers (number, reason, blocked_until, hits)
           VALUES ($1, $2, CASE WHEN $3::int IS NULL THEN NULL
                                ELSE now() + ($3::int * interval '1 second') END, 1)
           ON CONFLICT (number) DO UPDATE
             SET hits = blocked_callers.hits + 1,
                 reason = EXCLUDED.reason,
                 blocked_until = CASE
                   WHEN blocked_callers.blocked_until IS NULL THEN NULL
                   ELSE GREATEST(blocked_callers.blocked_until, EXCLUDED.blocked_until) END""",
        caller,
        reason[:500],
        seconds,
    )


async def unblock(pool, caller: str) -> None:
    await pool.execute("DELETE FROM blocked_callers WHERE number = $1", caller)


async def _recent_attempts(pool, caller: str) -> int:
    row = await pool.fetchrow(
        """SELECT count(*) AS n FROM call_attempts
           WHERE caller_number = $1 AND at > now() - ($2::int * interval '1 second')""",
        caller,
        RATE_WINDOW_SEC,
    )
    return int(row["n"]) if row else 0


async def _already_counted(pool, caller: str) -> bool:
    """同じ呼を2回数えないための照合。通常の着信は hookd (/guard) と agent (最終防衛線) の
    **両方**を通るので、素直に数えるとレート制限が半分の件数で発動してしまう。
    直前に hookd が同じ発信者を記録していれば「同じ呼の2度目の観測」とみなす。"""
    row = await pool.fetchrow(
        """SELECT 1 FROM call_attempts
           WHERE caller_number = $1 AND source = 'hookd'
             AND at > now() - ($2::int * interval '1 second') LIMIT 1""",
        caller,
        ATTEMPT_DEDUP_SEC,
    )
    return row is not None


async def _storm_count(pool) -> int:
    row = await pool.fetchrow(
        """SELECT count(*) AS n FROM call_attempts
           WHERE at > now() - ($1::int * interval '1 second')""",
        STORM_WINDOW_SEC,
    )
    return int(row["n"]) if row else 0


async def evaluate(pool, caller_raw: str | None, callee_raw: str | None, source: str) -> Verdict:
    """着信1件の総合判定。失敗しても通話は落とさない (fail-open) — 呼び出し側で例外を握る。

    判定の順序: 免除リスト → パターン → ブロックリスト → レート制限 → (通した場合) ストーム監視。
    どの経路でも call_attempts に1行残す (レート制限とストーム判定の母数になる)。
    """
    caller = normalize(caller_raw)
    callee = normalize(callee_raw)

    if caller and caller in allow_numbers():
        return Verdict()  # テスト通話等 — 試行ログにも載せない (レート制限を汚さない)

    # hookd が数秒前に同じ呼を記録済みなら、これはその呼の2度目の観測 (通常の着信は両層を通る)。
    # 判定自体は行うが、試行ログには足さない — レート制限が半分の件数で誤発動しないように
    counted_already = source != "hookd" and bool(caller) and await _already_counted(pool, caller)

    verdict = classify(caller_raw, callee_raw)

    if not verdict.reject and caller and await is_blocked(pool, caller):
        verdict = Verdict(
            reject=True,
            kind="blocked_repeat",
            severity="warn",
            detail=f"ブロック中の番号 {caller} からの再着信",
        )

    if not verdict.reject and caller and not counted_already:
        n = await _recent_attempts(pool, caller)
        if n + 1 > RATE_MAX:
            verdict = Verdict(
                reject=True,
                kind="rate_limit",
                severity="warn",
                detail=(
                    f"{caller} から {RATE_WINDOW_SEC // 60}分間に {n + 1} 件 "
                    f"(上限 {RATE_MAX} 件) — {BLOCK_SEC // 60}分間ブロックする"
                ),
            )

    # 国際表記で届いた国内番号。⚠拒否はしない (came_via_intl の注記参照) が、
    # normalize() が畳んでしまう前の事実なので**ここで拾わないと二度と分からない**。
    # 通した呼にも印を残す — 後から「あの着信は国際経由だったか」を追えるように
    if came_via_intl(caller_raw):
        verdict.extra = {**verdict.extra, "via_intl": True, "caller_raw": str(caller_raw)[:40]}
        if not verdict.reject:
            await record_event(
                pool,
                Verdict(
                    kind="intl_domestic_caller",
                    severity="warn",
                    detail=(
                        f"国内番号 {caller} が国際表記 ({str(caller_raw)[:40]}) で届いた。"
                        "通常の国内着信では起きない — 国際ゲートウェイ経由の可能性"
                    ),
                    action="alert",
                    extra={"caller_raw": str(caller_raw)[:40]},
                ),
                caller, callee, source,
            )
            log.warning("SECURITY intl_domestic_caller: %s (raw=%s)", caller, caller_raw)

    verdict.action = "allow"
    if verdict.reject:
        verdict.action = "reject" if ENFORCE else "observe"
        # 再着信でも呼ぶ — block() は ON CONFLICT で期限を延ばすので、叩き続ける相手ほど長く閉め出される
        if caller:
            await block(pool, caller, f"{verdict.label}: {verdict.detail}", BLOCK_SEC)

    if not counted_already:
        await _record_attempt(pool, caller, callee, source, verdict)

    if verdict.reject:
        await record_event(pool, verdict, caller, callee, source)
        log.warning(
            "SECURITY %s: %s (from=%s to=%s source=%s)",
            verdict.action, verdict.detail, caller or caller_raw, callee or "-", source,
        )
    elif not counted_already:
        await _check_storm(pool, source)

    if not ENFORCE:
        verdict.reject = False  # 様子見モード: 記録と通知だけ行い、呼はつなぐ
    return verdict


async def _check_storm(pool, source: str) -> None:
    """短時間に着信が集中している = 発信者番号を変えながらのスキャン。
    番号ごとのレート制限では捕まらないので、総量でも見張って警報だけ上げる
    (ここで自動遮断すると、たまたま重なった正常な着信まで落ちかねない)。"""
    n = await _storm_count(pool)
    if n <= STORM_MAX:
        return
    verdict = Verdict(
        reject=False,
        kind="storm",
        severity="critical",
        detail=(
            f"直近{STORM_WINDOW_SEC // 60}分の着信が {n} 件 (通常時の想定 {STORM_MAX} 件以下)。"
            "発信者番号を変えながらのSIPスキャンの可能性 — ファイアウォールとtrunkの"
            "allowed_addressesを確認すること"
        ),
        action="alert",
    )
    await record_event(pool, verdict, "", "", source)
    log.warning("SECURITY storm: %s", verdict.detail)


async def prune(pool) -> None:
    """試行ログの掃除 (常駐VMなので放っておくと増え続ける)。"""
    await pool.execute(
        "DELETE FROM call_attempts WHERE at < now() - ($1::int * interval '1 day')",
        ATTEMPT_RETENTION_DAYS,
    )


# ------------------------------------------------------------------- 通知

SEVERITY_ICON = {"critical": "🚨", "warn": "⚠", "info": "ℹ"}


def format_notification(rows: list) -> str:
    """Webhookに投げる本文。攻撃中は1件1通知だと埋もれるのでまとめて1通にする。"""
    lines = ["🚨 **フラグメント 警備アラート** — 不審な着信を検知しました"]
    for r in rows:
        icon = SEVERITY_ICON.get(r["severity"], "⚠")
        who = r["caller_number"] or "非通知/不明"
        to = f" → {r['callee_number']}" if r["callee_number"] else ""
        times = f" ×{r['count']}" if r["count"] > 1 else ""
        act = {"reject": "自動拒否", "observe": "検知のみ (様子見モード)", "alert": "警報"}.get(
            r["action"], r["action"]
        )
        lines.append(f"{icon} [{KIND_LABELS.get(r['kind'], r['kind'])}] {who}{to}{times} — {act}")
        lines.append(f"　{r['detail']}")
    url = os.environ.get("DASHBOARD_URL", "").rstrip("/")
    if url:
        lines.append(f"\n管制室: {url}/security")
    return "\n".join(lines)


async def notify_pending(pool, session) -> int:
    """未通知 (または前回通知から10分以上続いている) イベントをまとめて1通で送る。
    戻り値は通知したイベント数。Webhook未設定なら何もしない (0を返す)。"""
    url = os.environ.get("SECURITY_WEBHOOK_URL", "").strip()
    if not url:
        return 0
    rows = await pool.fetch(
        """SELECT id, kind, severity, action, caller_number, callee_number, detail, count
           FROM security_events
           WHERE notified_at IS NULL
              OR last_at > notified_at + interval '10 minutes'
           ORDER BY id LIMIT 20"""
    )
    if not rows:
        return 0
    body = format_notification(rows)
    # Slack は text、Discord は content。どちらもURLで判別できる
    payload = {"text": body} if "hooks.slack.com" in url else {
        "content": body[:1900],
        "username": "フラグメント 警備",
    }
    async with session.post(url, json=payload, timeout=10) as res:
        if res.status >= 300:
            log.warning("security webhook failed: HTTP %s", res.status)
            return 0
    await pool.execute(
        "UPDATE security_events SET notified_at = now() WHERE id = ANY($1::bigint[])",
        [r["id"] for r in rows],
    )
    log.info("security webhook: %d件を通知", len(rows))
    return len(rows)
