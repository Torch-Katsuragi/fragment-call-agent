# フラグメント (Call-Agent)

電話に AI が一次応対し、用件を預かって持ち主に渡すための、セルフホスト型の電話エージェントです。
日本の電話回線 (SIP で収容できる 050 / 固定番号) 向けに作っています。

名前の由来: 自分の「断片」が電話に出る × 通話中に浮かぶ「思考の断片」(吹き出し)。

## できること

- **AI の一次応対** — 着信に AI が出て、相手の話を聞き、用件を預かる。解決までは狙わない
  (「対話できる留守番電話」)。挨拶や録音の告知は決まった文言で確実に流す
- **3つの応答モード** — 不在 (すぐ AI) / スタンバイ (数秒鳴らして、出なければ AI) /
  自分で出る (AI は出ない)。スマホと管制室から切り替えられる
- **着信の3択** — 出る / AI に任せる (待たずに AI) / 切る (相手ごと切る)
- **着信前の下調べ** — 電話帳 (Markdown) と過去の通話、発信者番号の web 検索を並行で回し、
  着信画面に「番号検索中…」→ 事業者名、と段階的に出す。明らかな営業・詐欺は AI につなぐ前に切れる
- **ライブ文字起こしと取り次ぎ** — 応対中の会話をリアルタイムで表示。途中で本人が入れば AI は聞き役に下がる。
  本人の「耳打ち」を相手に聞こえない形で AI に渡せる
- **吹き出し (フラグメント)** — 会話を別の AI が見張り、ワークスペースの情報から本人向けの考察を浮かべる
- **終話後の通知** — AI が受けた通話は、誰から・何の用だったかの要約を1枚の通知にする
- **専用 Android アプリ** — 普通の電話アプリと同じ着信体験 (ロック中は全画面、使用中はバナー)。
  マナー / おやすみモードの扱いも OS の通話と同じ
- **警備** — SIP の不正利用 (内線番号の総当たり・国際中継・連続着信) を呼び出し音より前に検知して落とす
- **録音** — 通話は冒頭で告知したうえで録音する

## 構成

```
回線 (SIP)                              サーバー (Docker Compose)                     クライアント
─────────                              ─────────────────────────                     ────────────
050 等 ──REGISTER──► Asterisk ──SIP──► LiveKit SIP ──► LiveKit ◄── agent (応対 AI)
                     │  ダイヤルプラン                    │  1通話 = 1ルーム          ◄──► 管制室 (Next.js)
                     │  録音告知・呼び出し音               │                          ◄──► Android アプリ
                     └─ hookd (着信フック API) ◄───────────┘
                         警備・下調べ起動・受話待ち・端末へのプッシュ
                     directory-agent (下調べ・番号検索・吹き出し・要約)
                     PostgreSQL (通話・文字起こし・警備ログ)
```

詳しい設計は [DESIGN.md](DESIGN.md)。

| 部品 | 場所 | 役割 |
|---|---|---|
| Asterisk | `infra/asterisk/` | 回線に REGISTER して着信を受け、LiveKit へ渡す。録音告知・呼び出し音・受話待ち |
| LiveKit / LiveKit SIP | `infra/livekit/` | 音声のルーム (1通話 = 1ルーム) |
| agent | `services/agent/agent.py` | 応対 AI (Gemini Live API)。文字起こし・取り次ぎ・耳打ちの反映 |
| hookd | `services/agent/hookd.py` | ダイヤルプランと端末から叩かれる API。警備の関門 (`security.py`)、FCM プッシュ |
| directory-agent | `services/directory-agent/worker.py` | ワークスペースの番人。下調べ・番号検索・吹き出し・終話の要約 |
| 管制室 | `services/dashboard/` | Web UI。着信バナー、ライブの通話画面、履歴、設定、端末のペアリング |
| Android アプリ | `clients/android/` | 子機。着信・取り次ぎ・通話・発信 |

## 必要なもの

- Linux ホスト (Docker / Docker Compose)。公開の管制室を置くならドメインと HTTPS (同梱の Caddy 設定を参考に)
- SIP で収容できる電話回線 (REGISTER 型。Twilio Elastic SIP Trunking のような peer 型にも対応)
- Google の API
  - Gemini API キー (応対・下調べ・番号検索・要約)
  - Cloud Text-to-Speech のサービスアカウント (定型文の声。Gemini TTS も選べる)
  - Google OAuth クライアント (管制室のログイン)
  - Firebase Cloud Messaging (任意。スマホを即座に起こす。無くてもロングポーリングで動く)
- ワークスペース用のディレクトリ (電話帳・通話記録・設定の Markdown を置く。Google Drive などで同期してよい)

## 導入

1. **設定** — `.env.example` を `.env` にコピーして埋める。最低限:
   `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET`、回線の SIP 情報、`GOOGLE_API_KEY`、
   `OWNER_NAME` (持ち主の呼び名)、`OUTBOUND_CALLERID` (発信で名乗る番号)
2. **Asterisk の設定を生成** — `python3 infra/asterisk/render_config.py`
   (PowerShell 版 `Render-Config.ps1` もある)。`.env` の SIP 情報から `pjsip.conf` を作る (生成物は git 管理外)
3. **起動** — `infra/` で
   ```bash
   docker compose --env-file ../.env --profile asterisk --profile vm-worker up -d
   sh livekit/create-trunk.sh   # LiveKit の inbound trunk と dispatch rule を登録 (redis を作り直したら毎回)
   ```
   `docker compose exec asterisk asterisk -rx "pjsip show registrations"` が `Registered` になれば回線は通っている
4. **管制室** — `services/dashboard/` で `npm install && npm run build && npm start`。
   認証まわり (`AUTH_SECRET`、Google OAuth、`ALLOWED_EMAILS`、公開時は `AUTH_URL`) は
   `services/dashboard/.env.local` に置く。`ALLOWED_EMAILS` が空なら誰もログインできない
5. **スマホ** — `clients/android/` をビルドしてインストールし、管制室の「設定 → スマホアプリ → QRを表示」を
   アプリで読むとペアリングされる
6. **ワークスペース** — `FRAGMENT_WORKSPACE_HOST` に置き場所を指定する。`連絡先/<番号>.md` が電話帳、
   `設定/` に応対 AI の人格と情報の扱いを書く (無ければ `services/agent/prompts/` の既定が使われる)

動作確認は `infra/scripts/testcall.sh` (相手役 AI が架空の番号から掛けてくる。実回線は使わない) と
`infra/scripts/run_scenarios.py` (決まった筋書きで応対を試験する)。

## 警備

SIP のポートを公開すると、内線番号の総当たりや国際番号への中継を狙うスキャナーがすぐに来ます。
判定は `services/agent/security.py` にまとめ、2か所で使います。

| 層 | 止まる場所 | 効果 |
|---|---|---|
| hookd `/guard` | 呼び出し音を鳴らす前 (ダイヤルプランの冒頭) | 下調べ (LLM) すら走らない |
| agent | 応対セッションを作る前 | 直接 LiveKit に来た呼も LLM を使わずに切る |

国際番号への中継・内線番号の総当たり・同一発信者の連続着信 (自動で時限ブロック)・着信ストーム (警報) を見ます。
非通知は通します。どの層も、警備自体が落ちているときは通話を通します (着信を止める方が損害が大きいため)。
記録は管制室の「警備」画面に出て、`SECURITY_WEBHOOK_URL` にも通知できます。
設定は `.env.example` の「警備」の節。`SECURITY_ENFORCE=0` で「検知と通知だけ」の慣らし運転ができます。

## リポジトリ構成

```
clients/android/      Android アプリ (Kotlin / Jetpack Compose / LiveKit Android SDK)
infra/
  docker-compose.yml  サーバー一式
  asterisk/           回線の仲介 (ダイヤルプラン・告知音声・呼び出し音)
  livekit/            LiveKit / LiveKit SIP の設定
  postgres/           スキーマ
  caddy/  gcp/        公開ホストの例 (Caddy / Terraform)
  scripts/            デプロイ・テスト通話・シナリオ試験など
services/
  agent/              応対 AI・hookd・警備・既定のプロンプト
  directory-agent/    ワークスペースの番人
  dashboard/          管制室 (Next.js)
```

## ライセンス

[GNU Affero General Public License v3.0](LICENSE)。
改変したものをネットワーク越しにサービスとして提供する場合も、その改変したソースを利用者に公開する必要があります。
