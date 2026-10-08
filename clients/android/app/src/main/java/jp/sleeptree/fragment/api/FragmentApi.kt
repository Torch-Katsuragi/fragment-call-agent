package jp.sleeptree.fragment.api

import android.util.Log
import jp.sleeptree.fragment.Prefs
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL

/**
 * 管制室 (Next.js) 越しに hookd を叩くクライアント。
 *
 * ⚠hookd は VM の 127.0.0.1:8790 でしか待ち受けていない (2026-07-26のトールフラウド事故以降、
 *   外に出す口は塞いである)。だからアプリは必ず管制室のAPIを経由する。
 *
 * ⚠認証は端末トークン。管制室は Google OAuth で全パスを保護している (`auth.ts` の authorized、
 *   本番は fail-closed) ので、**サーバー側に端末トークンを通す分岐が要る**。
 *   これは未実装 — 設計mdの「サーバー側の依存」を参照。
 */
class FragmentApi(private val prefs: Prefs) {

    /** 管制室の吹き出し (本人向けの分析)。⚠【本人限定】情報を含みうる — ロック中は出さない */
    data class Fragment(val kind: String, val title: String, val text: String, val id: Long = 0)

    /** 会話の1発話。speaker: caller / ai / user / whisper */
    data class Segment(val speaker: String, val text: String, val seq: Int = 0)

    /** 通話1本。一覧 (segments は直近3発話) と詳細 (segments/fragments は全部) で共用する */
    data class Call(
        val id: String,
        val number: String?,
        val name: String?,
        val room: String,
        /** epoch millis */
        val startedAt: Long,
        val endedAt: Long?,
        /** inbound / outbound */
        val direction: String,
        val segments: List<Segment> = emptyList(),
        val fragments: List<Fragment> = emptyList(),
        /** 担い手 (2026-09-26): ai / human / hold / connecting。詳細 (/api/calls/<id>) にだけ入る */
        val handler: String = "",
        val presence: List<Presence> = emptyList(),
        /** 要約 (worker が終話後に作る)。null = まだ無い、空 = 作れなかった */
        val summary: String? = null,
        /** 電話帳に無い番号の、番号検索で分かった名前 (事業者名など) */
        val lookupName: String? = null,
        /**
         * この通話で話した相手 (2026-09-26、calls.caller_label)。同じ番号でも通話ごとに違う (組織の番号)。
         * null = 電話帳の名前のままでよい
         */
        val callerLabel: String? = null,
    ) {
        val active: Boolean get() = endedAt == null
        val outbound: Boolean get() = direction == "outbound"
        val displayName: String get() = callerLabel ?: name ?: lookupName ?: number ?: "非通知"
        /** 電話帳 (または番号検索) の名前。callerLabel を大きく出すときに小さく添える */
        val registeredName: String? get() = name ?: lookupName
        /** 名前が分かっているか (番号を小さく添えるかどうか) */
        val hasName: Boolean get() = name != null || lookupName != null
        /** 本人が話した通話か (発話に user がある)。一覧の「誰が応対したか」に使う */
        val answeredByMe: Boolean get() = segments.any { it.speaker == "user" }
    }

    /** 発信の進み具合。status: dialing / answered / failed */
    data class Dialing(val number: String, val status: String, val reason: String)

    /** LiveKit への接続情報 */
    data class RoomToken(val url: String, val token: String)

    /**
     * 番号のweb検索 (2026-09-25)。電話帳に無い相手のときだけサーバーが付ける。
     * pending の間は着信画面に「番号検索中…」を出し、done で name に置き換える。
     */
    data class Lookup(val pending: Boolean, val name: String?, val verdict: String)

    data class Handoff(
        val room: String,
        val number: String,
        val name: String?,
        val reason: String,
        val expiresInSec: Double,
        /** 呼び出し中に判断材料として見せる分析 */
        val fragments: List<Fragment> = emptyList(),
        /** 直近の会話。鳴っている間も相手はAIと話し続けているのでポーリングで伸びる */
        val recent: List<Segment> = emptyList(),
        val lookup: Lookup? = null,
        /** ai = AI の取り次ぎ / manual = 保留中に人が呼んだ (2026-09-26)。by = 呼んだ端末の名前 */
        val kind: String = "ai",
        val by: String = "",
    ) {
        val manual: Boolean get() = kind == "manual"
    }

    /** ルームにいる端末 (2026-09-26)。role = talk (会話) / watch (視聴) */
    data class Presence(val id: String, val name: String, val role: String)

    /**
     * 電話帳の 1 件 (2026-09-26)。実体はサーバーの 連絡先/<番号>.md。
     * memo = 「メモ」の節 (着信のとき AI が読む相手ごとのメモ)。lastCallAt は epoch millis
     */
    data class Contact(
        val number: String,
        val name: String?,
        /** 読みがな (ひらがな)。並び順と検索に使う */
        val kana: String? = null,
        val memo: String,
        /** 「番号検索」の節。通話と食い違ったことは ⚠ の行で入っている */
        val lookup: String = "",
        /** 「話した人」の節 (組織の番号からかけてきた個人。"### 名前" の見出しごと) */
        val people: String = "",
        /** 「未整理」の節 (同じ人か迷って振り分けなかった名乗り。人が仕分ける) */
        val unsorted: String = "",
        /** 「食い違い」の節 (番号検索や登録済みの名前と、通話での名乗りが違った記録。⚠ の行) */
        val conflicts: String = "",
        /** 相手ごとの応答 (2026-09-30)。"人が出る" / "AIが出る" / null = 回線の設定に従う */
        val answer: String? = null,
        val callCount: Int,
        val lastCallAt: Long?,
        val lastCallId: String?,
    )

    /** 保留中に呼ぶ相手の候補。reason = AI が並べ替えた理由 (無いこともある) */
    data class Candidate(val id: String, val name: String, val role: String, val reason: String)

    data class ActiveCall(
        /** calls テーブルのid。管制室の通話画面URL (/call/<id>) に要る */
        val id: String,
        val room: String,
        val number: String,
        val name: String?,
        /** 担い手 (2026-09-26): ai / human / hold / connecting (services/agent/presence.py) */
        val handler: String = "ai",
        val aiOn: Boolean = true,
        val presence: List<Presence> = emptyList(),
    ) {
        val talkers: List<Presence> get() = presence.filter { it.role == "talk" }

        /** 待機中の端末に出す見出し。「AIが応対中」「〇〇が応対中」「保留中」 */
        val handlerLabel: String
            get() = when (handler) {
                "human" -> "${talkers.joinToString("・") { it.name.ifEmpty { "端末" } }.ifEmpty { "本人" }}が応対中"
                "hold" -> "保留中"
                "connecting" -> "接続中"
                else -> "AIが応対中"
            }
    }

    /**
     * まだAIが出ていない着信。⚠取り次ぎ (Handoff) とは別物 —
     * あちらは応対中の通話に本人が入る話で、こちらは本人が先に取る話。
     */
    data class Incoming(
        val number: String,
        val name: String?,
        /** standby (2秒だけ猶予) / manual (受話を待って鳴らし続ける) */
        val mode: String,
        /** あと何ミリ秒鳴らすか。⚠サーバーが決める — 端末が勝手に鳴らし続けると取れない着信になる */
        val ringMs: Long,
        val lookup: Lookup? = null,
        /** 録音告知中 (2026-09-25)。相手は出すが、出るボタンと着信音は告知が終わってから */
        val announcing: Boolean = false,
    )

    /** AI が受けて終わった通話 (2026-09-25)。終話通知の中身 */
    data class Ended(
        val id: String,
        val number: String,
        val name: String?,
        /** worker が作った要約。空 = 作れなかった */
        val summary: String,
        val durationSec: Int,
    )

    data class DeviceState(
        /** ロングポーリングの継続に使う指紋。次の呼び出しにそのまま渡す */
        val v: String,
        val handoff: Handoff?,
        val incoming: Incoming?,
        val activeCall: ActiveCall?,
        val answerMode: String,
        /** 直近に終わった通話 (新しい順、最大5件)。通知済みかどうかは端末が覚えている */
        val recentEnded: List<Ended> = emptyList(),
        val modeInfo: ModeInfo? = null,
    )

    /**
     * 応答モードの出どころと次の切り替わり (2026-09-29、hookd の schedule.py)。
     * source: schedule = 時間割どおり / manual = 手で切り替えた (nextAt まで) / fixed = 時間割なし
     */
    data class ModeInfo(
        val source: String,
        val scheduleEnabled: Boolean,
        /** epoch millis。null = 変わる予定なし */
        val nextAt: Long?,
        val nextMode: String?,
    )

    /** 受付時間の 1 日分の時間帯 ("08:00", "17:00")。終わりが始まり以前なら日をまたぐ */
    data class Block(val start: String, val end: String)

    /**
     * 端末が必要とする状態を1回で取る。
     *
     * ⚠アプリ専用に1本にまとめてある。管制室のブラウザ用エンドポイント (`/api/ringing` 等) を
     *   個別に叩くと往復が増えるうえ、画面都合の変更に引きずられるため。
     *
     * ⚠`waitSec` を渡すとロングポーリングになる (状態が変わるまで返らない)。
     *   スタンバイの猶予は2秒しかないので、**間隔ポーリングでは猶予をまるごと取りこぼす**。
     *   `since` には前回の `v` を渡すこと — 省くと毎回すぐ返って普通のポーリングに戻る。
     */
    fun deviceState(waitSec: Int = 0, since: String = ""): DeviceState? {
        val q = if (waitSec > 0) "?wait=$waitSec&v=${java.net.URLEncoder.encode(since, "UTF-8")}" else ""
        // 待つぶんだけ読み取りタイムアウトを伸ばす。⚠ここを伸ばし忘れると必ず例外で落ちる
        val body = request("GET", "/api/device/state$q", null, readTimeoutMs = 8000 + waitSec * 1000)
            ?: return null
        return try {
            val o = JSONObject(body)
            val h = o.optJSONObject("handoff")?.let {
                Handoff(
                    room = it.getString("room"),
                    number = str(it, "number"),
                    name = str(it, "name").ifEmpty { null },
                    reason = str(it, "reason"),
                    expiresInSec = it.optDouble("expires_in", 0.0),
                    fragments = it.optJSONArray("fragments").mapObjects { f ->
                        Fragment(
                            kind = str(f, "kind").ifEmpty { "info" },
                            title = str(f, "title"),
                            text = str(f, "text"),
                        )
                    },
                    recent = it.optJSONArray("recent").mapObjects { s ->
                        Segment(speaker = str(s, "speaker"), text = str(s, "text"))
                    },
                    lookup = lookup(it),
                    kind = str(it, "kind").ifEmpty { "ai" },
                    by = str(it, "by"),
                )
            }
            val a = o.optJSONObject("active_call")?.let {
                ActiveCall(
                    id = it.getString("id"),
                    room = it.getString("room"),
                    number = str(it, "number"),
                    name = str(it, "name").ifEmpty { null },
                    handler = str(it, "handler").ifEmpty { "ai" },
                    aiOn = it.optBoolean("ai_on", true),
                    presence = presence(it),
                )
            }
            val inc = o.optJSONObject("incoming")?.let {
                Incoming(
                    number = str(it, "number"),
                    name = str(it, "name").ifEmpty { null },
                    mode = str(it, "mode").ifEmpty { "standby" },
                    ringMs = it.optLong("ring_ms", 0L),
                    lookup = lookup(it),
                    announcing = str(it, "phase") == "announcing",
                )
            }
            val ended = o.optJSONArray("recent_ended").mapObjects {
                Ended(
                    id = it.getString("id"),
                    number = str(it, "number"),
                    name = str(it, "name").ifEmpty { null },
                    summary = str(it, "summary"),
                    durationSec = it.optInt("duration_sec", 0),
                )
            }
            DeviceState(
                v = str(o, "v"),
                recentEnded = ended,
                handoff = h,
                incoming = inc,
                activeCall = a,
                answerMode = o.optString("answer_mode", "away"),
                modeInfo = o.optJSONObject("mode_info")?.let {
                    ModeInfo(
                        source = str(it, "source").ifEmpty { "fixed" },
                        scheduleEnabled = it.optBoolean("schedule_enabled", false),
                        nextAt = if (it.isNull("next_at")) null else (it.optDouble("next_at") * 1000).toLong(),
                        nextMode = str(it, "next_mode").ifEmpty { null },
                    )
                },
            )
        } catch (e: Exception) {
            Log.w(TAG, "deviceState parse failed", e)
            null
        }
    }

    /** 通話の一覧 (新しい順)。segments に直近3発話が入る */
    /** before (epoch millis) を渡すとそれより前の通話 (ホームの無限スクロールの続き、2026-09-26) */
    fun calls(limit: Int = 50, before: Long? = null): List<Call>? {
        val q = before?.let { "&before=" + java.net.URLEncoder.encode(java.time.Instant.ofEpochMilli(it).toString(), "UTF-8") }.orEmpty()
        val body = request("GET", "/api/calls?limit=$limit$q", null) ?: return null
        return try {
            val a = org.json.JSONArray(body)
            (0 until a.length()).mapNotNull { a.optJSONObject(it)?.let { o -> parseCall(o, "preview") } }
        } catch (e: Exception) {
            Log.w(TAG, "calls parse failed", e)
            null
        }
    }

    /** 通話1本の全部 (発話・フラグメント) */
    fun call(id: String): Call? {
        val body = request("GET", "/api/calls/$id", null) ?: return null
        return try {
            parseCall(JSONObject(body), "segments")
        } catch (e: Exception) {
            Log.w(TAG, "call parse failed", e)
            null
        }
    }

    private fun parseCall(o: JSONObject, segKey: String) = Call(
        id = o.getString("id"),
        number = str(o, "caller_number").ifEmpty { null },
        name = str(o, "caller_name").ifEmpty { null },
        room = str(o, "room_name"),
        startedAt = time(str(o, "started_at")) ?: 0L,
        endedAt = time(str(o, "ended_at")),
        direction = str(o, "direction").ifEmpty { "inbound" },
        segments = o.optJSONArray(segKey).mapObjects { s ->
            Segment(speaker = str(s, "speaker"), text = str(s, "text"), seq = s.optInt("seq"))
        },
        fragments = o.optJSONArray("fragments").mapObjects { f ->
            Fragment(
                kind = str(f, "kind").ifEmpty { "info" },
                title = str(f, "title"),
                text = str(f, "text"),
                id = f.optLong("id"),
            )
        },
        handler = str(o, "handler"),
        presence = presence(o),
        summary = if (o.has("summary_md") && !o.isNull("summary_md")) o.optString("summary_md") else null,
        lookupName = str(o, "lookup_name").ifEmpty { null },
        callerLabel = str(o, "caller_label").ifEmpty { null },
    )

    private fun presence(o: JSONObject): List<Presence> =
        o.optJSONArray("presence").mapObjects { p ->
            Presence(id = str(p, "id"), name = str(p, "name"), role = str(p, "role"))
        }

    /** ISO 8601 → epoch millis。空・壊れた値は null */
    private fun time(s: String): Long? =
        if (s.isEmpty()) null else runCatching { java.time.Instant.parse(s).toEpochMilli() }.getOrNull()

    /** AIへの耳打ち (相手には聞こえない) */
    fun whisper(callId: String, text: String): Boolean =
        post("/api/calls/$callId/whisper", JSONObject().put("text", text)) != null

    /**
     * LiveKit のトークン。operator = 自分が話す (マイクを出す)。
     * ⚠agent は identity の `operator-` で本人の入室を知り、AI を聞き役に下げる (/api/token)
     */
    fun roomToken(room: String, operator: Boolean): RoomToken? {
        val q = "room=${java.net.URLEncoder.encode(room, "UTF-8")}" + if (operator) "&role=operator" else ""
        val body = request("GET", "/api/token?$q", null) ?: return null
        return try {
            val o = JSONObject(body)
            RoomToken(url = o.getString("url"), token = o.getString("token"))
        } catch (e: Exception) {
            null
        }
    }

    /**
     * AI 応答のオン・オフ (2026-09-26)。「AIに任せる」= オンにしてから会話を抜ける。
     * ⚠会話中の端末がある間はオンにしても AI は喋らない (抜けた時点で AI が戻る)
     */
    fun setAi(callId: String, on: Boolean): Boolean =
        post("/api/calls/$callId/ai", JSONObject().put("on", on)) != null

    /** 電話帳の全件。取れなければ null */
    fun phonebook(): List<Contact>? {
        val body = request("GET", "/api/phonebook", null) ?: return null
        return runCatching {
            JSONObject(body).optJSONArray("contacts").mapObjects {
                Contact(
                    number = str(it, "number"),
                    name = str(it, "name").ifEmpty { null },
                    kana = str(it, "kana").ifEmpty { null },
                    memo = str(it, "memo"),
                    lookup = str(it, "lookup"),
                    people = str(it, "people"),
                    unsorted = str(it, "unsorted"),
                    conflicts = str(it, "conflicts"),
                    answer = str(it, "answer").ifEmpty { null },
                    callCount = it.optInt("call_count", 0),
                    lastCallAt = time(str(it, "last_call_at")),
                    lastCallId = str(it, "last_call_id").ifEmpty { null },
                )
            }
        }.getOrNull()
    }

    /** 相手ごとの応答を変える (2026-09-30)。answer = "人が出る" / "AIが出る" / null (回線の設定に従う) */
    fun setContactAnswer(number: String, answer: String?): Boolean =
        post("/api/phonebook/answer", JSONObject().put("number", number).put("answer", answer ?: JSONObject.NULL)) != null

    /** スマホの電話帳から選んだ相手を登録する。@return 登録できた番号の数 (null = 失敗) */
    fun addContact(name: String, kana: String?, numbers: List<String>): Int? {
        val body = post(
            "/api/phonebook",
            JSONObject().put(
                "contacts",
                org.json.JSONArray().put(
                    JSONObject().put("name", name).put("kana", kana ?: "").put("numbers", org.json.JSONArray(numbers))
                ),
            ),
        ) ?: return null
        return runCatching { JSONObject(body).optJSONArray("saved")?.length() ?: 0 }.getOrNull()
    }

    /** 保留中に呼ぶ相手の候補 (AI が会話から並べ替えたもの)。取れなければ null */
    fun candidates(callId: String): List<Candidate>? {
        val body = request("GET", "/api/calls/$callId/candidates", null) ?: return null
        return runCatching {
            JSONObject(body).optJSONArray("candidates").mapObjects {
                Candidate(str(it, "id"), str(it, "name"), str(it, "role"), str(it, "reason"))
            }
        }.getOrNull()
    }

    /** 相手を指定して呼ぶ。宛先の端末で取り次ぎと同じ呼び出しが鳴る。targets が空なら全端末 */
    fun callout(callId: String, targets: List<String>): Boolean =
        post("/api/calls/$callId/callout", JSONObject().put("targets", org.json.JSONArray(targets))) != null

    /** 発信する。⚠通話料がかかる (確認は画面側)。@return null = 受け付けた / 文字列 = 断られた理由 */
    fun dial(number: String): String? {
        val (code, body) = requestFull("POST", "/api/dial", JSONObject().put("number", number))
        if (code in 200..299) return null
        return body?.let { runCatching { JSONObject(it).optString("error") }.getOrNull() }
            ?.takeIf { it.isNotEmpty() }
            ?: "発信できませんでした"
    }

    /** 発信中の状態。発信していなければ null */
    fun dialing(): Dialing? {
        val body = request("GET", "/api/dial", null) ?: return null
        return try {
            val d = JSONObject(body).optJSONObject("dialing") ?: return null
            Dialing(number = str(d, "number"), status = str(d, "status"), reason = str(d, "reason"))
        } catch (e: Exception) {
            null
        }
    }

    /** 応答モード (away / standby / manual)。取れなければ null */
    fun answerMode(): String? =
        request("GET", "/api/settings", null)?.let {
            runCatching { JSONObject(it).optString("answer_mode") }.getOrNull()?.ifEmpty { null }
        }

    /** 取り次ぎに応答した。AI側の request_handoff ツールがこれを見て「つながった」を受け取る */
    fun acceptHandoff(room: String): Boolean =
        post("/api/device/handoff/accept", JSONObject().put("room", room)) != null

    /**
     * まだAIが出ていない着信に本人が出た。
     * ⚠スタンバイでは猶予が2秒。ダイヤルプランがロングポーリングで待っているので、
     *   押された瞬間に投げること (accept と違い、遅れるとAIに渡ってしまい取り返せない)
     */
    /** 着信への答え (2026-09-25)。action = ai (待たずに AI) / reject (相手ごと切る) */
    fun decide(number: String, action: String): Boolean =
        post("/api/device/decide", JSONObject().put("number", number).put("action", action)) != null

    /** 取り次ぎに「AIに続けさせる」(2026-09-25)。AI はすぐ「つかまりませんでした」と伝える */
    fun declineHandoff(room: String): Boolean =
        post("/api/device/handoff/decline", JSONObject().put("room", room)) != null

    fun pickup(number: String): Boolean =
        post("/api/device/pickup", JSONObject().put("number", number)) != null

    /** フラグメントが受けている番号 (2026-09-25)。設定画面の「番号」に並べる */
    data class Line(val id: String, val label: String, val number: String, val role: String)

    /** 設定済みの回線の一覧。取れなければ null (空リストは「回線が無い」) */
    fun lines(): List<Line>? =
        deviceConfig()?.let { c ->
            c.optJSONArray("lines").mapObjects {
                Line(str(it, "id"), str(it, "label"), str(it, "number"), str(it, "role"))
            }
        }

    /** 端末向けの接続設定 (firebase 等)。取れなければ null */
    fun deviceConfig(): JSONObject? =
        request("GET", "/api/device/config", null)?.let {
            try { JSONObject(it) } catch (e: Exception) { null }
        }

    /** FCM トークンを登録する。hookd が着信・取り次ぎの瞬間にこの宛先へ「起きろ」を送る */
    fun registerPush(token: String): Boolean =
        post("/api/device/push", JSONObject().put("token", token).put("platform", "android")) != null

    /**
     * この端末のログインを切る (2026-10-04)。⚠ログインし直す・別のサーバーにつなぐ前に、古いログインのまま呼ぶ。
     * 呼ばずに替えると、前の管制室に端末の登録 (起こす宛先) が残り「出られる端末」として数えられ続けた
     */
    fun logout(): Boolean =
        post("/api/device/logout", JSONObject()) != null

    /** 通話を切る (LiveKit のルームを消す → SIP 側にも BYE)。管制室の「通話終了」と同じ口 */
    fun hangupCall(callId: String): Boolean =
        post("/api/calls/$callId/hangup", JSONObject()) != null

    /** この端末の一時停止をサーバーに伝える。token は FCM トークン (端末の識別子) */
    fun setPaused(token: String, paused: Boolean): Boolean =
        post("/api/device/pause", JSONObject().put("token", token).put("paused", paused)) != null

    /**
     * 自分の受付時間 (2026-09-29)。曜日キー (mon…sun, hol) → 時間帯。空 = いつでも。取れなければ null
     */
    fun myHours(): Map<String, List<Block>>? {
        val body = request("GET", "/api/members/me/hours", null) ?: return null
        return try {
            val days = JSONObject(body).optJSONObject("hours")?.optJSONObject("days") ?: return emptyMap()
            days.keys().asSequence().associateWith { k ->
                days.optJSONArray(k).mapObjects { Block(str(it, "start"), str(it, "end")) }
            }
        } catch (e: Exception) {
            null
        }
    }

    /** 自分の受付時間を保存する。空なら「いつでも」。@return 失敗時の理由 (成功なら null) */
    fun setMyHours(days: Map<String, List<Block>>): String? {
        val d = JSONObject()
        for ((k, list) in days) {
            if (list.isEmpty()) continue
            d.put(k, org.json.JSONArray(list.map { JSONObject().put("start", it.start).put("end", it.end) }))
        }
        val hours: Any = if (d.length() == 0) JSONObject.NULL else JSONObject().put("days", d)
        val (code, text) = requestFull("PUT", "/api/members/me/hours", JSONObject().put("hours", hours))
        if (code in 200..299) return null
        return runCatching { JSONObject(text ?: "").optString("error") }.getOrNull()?.ifEmpty { null }
            ?: "保存できませんでした"
    }

    /** 応答モードを変える。away / standby / manual の3値 */
    fun setAnswerMode(mode: String): Boolean =
        post("/api/device/mode", JSONObject().put("mode", mode)) != null

    /**
     * JSONの文字列を安全に取る。
     *
     * ⚠**`optString` はJSONの null を文字列 "null" に変換する** (org.json の仕様)。
     *   `.ifEmpty { null }` では弾けず、実機の着信画面に発信者名として **`null` と表示された**
     *   (2026-08-01実測)。`isNull` で先に落とす。
     */
    private fun lookup(o: JSONObject): Lookup? =
        o.optJSONObject("lookup")?.let {
            Lookup(
                pending = str(it, "status") == "pending",
                name = str(it, "name").ifEmpty { null },
                verdict = str(it, "verdict").ifEmpty { "unknown" },
            )
        }

    private fun str(o: JSONObject, key: String): String =
        if (o.isNull(key)) "" else o.optString(key)

    private inline fun <T> org.json.JSONArray?.mapObjects(f: (JSONObject) -> T): List<T> {
        if (this == null) return emptyList()
        val out = ArrayList<T>(length())
        for (i in 0 until length()) this.optJSONObject(i)?.let { out.add(f(it)) }
        return out
    }

    private fun post(path: String, body: JSONObject): String? = request("POST", path, body)

    private fun request(
        method: String,
        path: String,
        body: JSONObject?,
        readTimeoutMs: Int = 8000,
    ): String? {
        val (code, text) = requestFull(method, path, body, readTimeoutMs)
        return if (code in 200..299) text else null
    }

    /** 失敗時の本文も要る呼び出し用 (発信の断り理由など)。code = -1 は通信自体の失敗 */
    private fun requestFull(
        method: String,
        path: String,
        body: JSONObject?,
        readTimeoutMs: Int = 8000,
    ): Pair<Int, String?> {
        val base = prefs.baseUrl
        if (base.isEmpty()) return -1 to null
        var conn: HttpURLConnection? = null
        return try {
            conn = (URL(base + path).openConnection() as HttpURLConnection).apply {
                requestMethod = method
                connectTimeout = 5000
                readTimeout = readTimeoutMs
                setRequestProperty("Authorization", "Bearer ${prefs.deviceToken}")
                // 端末の id (管制室の lib/device.ts)。⚠管制室はログインに紐づいた id を正にする。
                //   名前と役割は送らない (2026-10-04、端末は持たない。表示はメンバーの名前)
                setRequestProperty("X-Device-Id", prefs.deviceId)
                setRequestProperty("Accept", "application/json")
                if (body != null) {
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json")
                }
            }
            if (body != null) {
                conn.outputStream.use { it.write(body.toString().toByteArray()) }
            }
            val code = conn.responseCode
            if (code == 401 && prefs.deviceToken.isNotEmpty()) {
                // ⚠ログインが効かない (旧式のペアリングのまま・ログインを切られた・締め出された)。
                //   黙って失敗し続けないよう、この端末のログインを消す。次にアプリを開くとログイン画面になる
                //   (2026-09-26、旧式のペアリングをやめたとき)
                Log.w(TAG, "$method $path -> 401: ログインを消す")
                prefs.deviceToken = ""
            }
            if (code !in 200..299) {
                // ⚠それ以外の失敗はログだけ出して黙って諦める
                //   (呼び出しを止めてしまうより、次のポーリングで回復する方がよい)
                Log.w(TAG, "$method $path -> HTTP $code")
                return code to runCatching { conn.errorStream?.bufferedReader()?.use(BufferedReader::readText) }.getOrNull()
            }
            code to conn.inputStream.bufferedReader().use(BufferedReader::readText)
        } catch (e: Exception) {
            Log.w(TAG, "$method $path failed: ${e.message}")
            -1 to null
        } finally {
            conn?.disconnect()
        }
    }

    companion object {
        private const val TAG = "FragmentApi"
    }
}
