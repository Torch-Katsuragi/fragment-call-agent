# フラグメント (Call-Agent)

電話に AI が一次応対し、用件を預かって持ち主に渡すための、セルフホスト型の電話エージェントです。
日本の電話回線 (SIP で収容できる 050 / 固定番号) 向けに作っています。

名前の由来: 自分の「断片」が電話に出る × 通話中に浮かぶ「思考の断片」(吹き出し)。

## できること

- **AI の一次応対** — 着信に AI が出て、相手の話を聞き、用件を預かる。解決までは狙わない
  (「対話できる留守番電話」)。挨拶や録音の告知は決まった文言で確実に流す
- **3つの応答モードと時間割** — 不在 (すぐ AI) / スタンバイ (数秒鳴らして、出なければ AI) /
  自分で出る (AI は出ない)。スマホと管制室から手動で切り替えられるほか、曜日・時間帯・祝日・休業日ごとの
  時間割で自動切替でき、電話帳で相手ごとの応答（「人が出る」「AIが出る」）も上書きできる
- **着信の3択** — 出る / AI に任せる (待たずに AI) / 切る (相手ごと切る)
- **着信前の下調べ** — 電話帳 (Markdown) と過去の通話、発信者番号の web 検索を並行で回し、
  着信画面に「番号検索中…」→ 事業者名、と段階的に出す。明らかな営業・詐欺は AI につなぐ前に切れる
- **ライブ文字起こし・取り次ぎ・保留** — 応対中の会話をリアルタイムで表示。途中で持ち主やメンバーが入れば
  AI は聞き役に下がり、会話中の人が全員抜ければ保留音が流れて一定時間後に AI が自動で引き取る。
  相手に聞こえない「耳打ち」を AI に渡すこともできる
- **2段階マルチモーダル文字起こし補正** — 通話中は発話区間の音声を Gemini Flash で即時補正し、
  終話後はステレオ録音全体を Gemini Pro で全文清書することで、固有名詞や数字の取りこぼしを抑える
- **吹き出し (フラグメント)** — 会話を別の AI が見張り、ワークスペースの情報から応対者向けの考察を浮かべる
- **終話後の通知** — AI が受けた通話は、誰から・何の用だったかの要約を1枚の通知にする
- **メンバー招待・権限管理** — Google アカウント単位でメンバーを招待し、4段階ロール
  (オーナー / 管理 / 応対 / 閲覧)、人ごとの受付時間・担当、端末ごとの着信一時停止を管理できる
- **専用 Android アプリ** — 普通の電話アプリと同じ着信体験 (ロック中は全画面、使用中はバナー)。
  マナー / おやすみモードの扱いも OS の通話と同じ
- **警備** — SIP の不正利用 (内線番号の総当たり・国際中継・連続着信) を呼び出し音より前に検知して落とす
- **ステレオ録音** — 冒頭で告知したうえで録音し、終話後に左=相手・右=AI/本人のステレオ Ogg Opus に圧縮して
  ワークスペースへ保存する。管制室から再生・保存期間の設定・期間指定での削除ができる

## 構成

単一ホスト完結 (Docker Compose 内のローカル worker で `agent` を常駐) でも動かせますし、
メモリ消費の大きい音声 AI (`agent`) だけを **Cloud Run (HTTP プッシュ駆動 + Direct VPC Egress)** に逃がし、
ホスト側の `calld` から先行ウォームアップ・ディスパッチ・死活監視を行う省メモリ構成 (2GB の `e2-small` VM で稼働) にも対応しています。

```
回線 (SIP)                   サーバー (Docker Compose)                                  クライアント / Cloud Run
─────────                   ─────────────────────────                                  ───────────────────────
050 等 ──REGISTER──► Asterisk ──SIP──► LiveKit SIP ──► LiveKit ◄──────────────────────► 管制室 (Next.js)
                     │  ダイヤルプラン                    │  1通話 = 1ルーム             ◄► Android アプリ
                     │  録音告知・呼び出し音               │                              │
                     └─ hookd (着信フック API) ──────────► calld (ディスパッチ・死活監視) │
                         警備・下調べ起動・受話待ち・FCM       │  先行ウォームアップ      │
                     directory-agent (下調べ・吹き出し・清書)  │  agent_lease / heartbeat │
                     PostgreSQL (通話・メンバー・警備ログ)     ▼                          │
                                                         agent (応対 AI: ローカル worker または Cloud Run HTTP)
```

詳しい設計は [DESIGN.md](DESIGN.md)。

| 部品 | 場所 | 役割 |
|---|---|---|
| Asterisk | `infra/asterisk/` | 回線に REGISTER して着信を受け、LiveKit へ渡す。録音告知・呼び出し音・受話待ち |
| LiveKit / LiveKit SIP | `infra/livekit/` | 音声のルーム (1通話 = 1ルーム) |
| agent | `services/agent/agent.py` | 応対 AI (Gemini Live API)。ローカル worker と Cloud Run HTTP プッシュの両対応 |
| calld | `services/agent/calld.py` | Cloud Run HTTP モード時のディスパッチ・先行ウォームアップ・世代トークン (`agent_lease`) 死活監視 |
| hookd | `services/agent/hookd.py` | ダイヤルプランと端末から叩かれる API。警備 (`security.py`)、時間割判定 (`schedule.py`)、FCM プッシュ |
| directory-agent | `services/directory-agent/worker.py` | ワークスペースの番人。下調べ・番号検索・吹き出し・ステレオ Ogg 変換・全文清書・要約 |
| 管制室 | `services/dashboard/` | Web UI。着信バナー、ライブ通話画面、履歴・録音再生、メンバー招待・時間割・設定 |
| Android アプリ | `clients/android/` | 子機。Google ログインで参加し、着信・取り次ぎ・保留転送・通話・発信を担う |

## 必要なもの

- Linux ホスト (Docker / Docker Compose)。公開の管制室を置くならドメインと HTTPS (同梱の Caddy 設定を参考に)
- SIP で収容できる電話回線 (REGISTER 型。Twilio Elastic SIP Trunking のような peer 型にも対応)
- Google Cloud / AI の認証情報
  - Gemini API キー (`GOOGLE_API_KEY`) **または Vertex AI プロジェクト (`VERTEX_PROJECT`)**
  - Cloud Text-to-Speech のサービスアカウント (定型文の声。Gemini TTS / Aivis 等も選べる)
  - Google OAuth クライアント (管制室および Android アプリのログイン)
  - Firebase Cloud Messaging (任意。スマホを即座に起こす。無くてもロングポーリングで動く)
- ワークスペース用のディレクトリ (電話帳・通話記録・録音・設定を置く。Google Drive などで同期してよい)

## 導入

1. **設定** — `.env.example` をリポジトリ直下の `.env` にコピーして埋める (設定は `.env` に一元化されており、
   `services/dashboard/.env.local` は使わない)。最低限:
   - `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET`、回線の SIP 情報、`OUTBOUND_CALLERID` (発信で名乗る番号)
   - `GOOGLE_API_KEY` または `VERTEX_PROJECT`、`OWNER_NAME` (持ち主の呼び名)
   - 管制室の認証: `AUTH_SECRET`、`AUTH_GOOGLE_ID`、`AUTH_GOOGLE_SECRET`、`ALLOWED_EMAILS` (最初のオーナーになる Google アカウント)、公開時は `AUTH_URL`
   - 単一ホストでは `PG_BIND_IP=127.0.0.1` / `HOOKD_BIND_IP=127.0.0.1` のまま使う (Cloud Run Direct VPC Egress を併用する場合のみ、VPC FW で外部遮断したうえで `0.0.0.0` にする)
2. **Asterisk の設定を生成** — `python3 infra/asterisk/render_config.py`
   (PowerShell 版 `Render-Config.ps1` もある)。`.env` の SIP 情報から `pjsip.conf` を作る (生成物は git 管理外)
3. **起動** — `infra/` で
   ```bash
   # 単一ホスト完結 (agent もローカルコンテナで常駐させる場合)
   docker compose --env-file ../.env --profile asterisk --profile vm-worker --profile local-agent up -d
   sh livekit/create-trunk.sh   # LiveKit の inbound trunk と dispatch rule を登録 (redis を作り直したら毎回)
   ```
   `docker compose exec asterisk asterisk -rx "pjsip show registrations"` が `Registered` になれば回線は通っている。
   音声 AI を Cloud Run に分離する場合は `--profile local-agent` を外して起動し、`infra/scripts/deploy_cloud_agent.sh` でデプロイした URL を `.env` の `CLOUD_AGENT_URL` に設定する
4. **管制室** — `services/dashboard/` で `npm install && npm run build && npm start` (リポジトリ直下の `.env` を自動で読む)。
   メンバー表が空の初回ログイン時に、`ALLOWED_EMAILS` のアドレスが最初のオーナー (`owner`) として登録される
5. **スマホ** — `clients/android/` をビルドしてインストールし、サーバー URL を入力して Google ログインする。
   最初のオーナーは `ALLOWED_EMAILS` の Google アカウントでそのまま入れ、追加メンバーは管制室の「設定 → メンバー」から招待リンクを発行して参加させる
6. **ワークスペース** — `FRAGMENT_WORKSPACE_HOST` (ホスト実行時は `FRAGMENT_WORKSPACE`) に置き場所を指定する:
   - `連絡先/<番号>/<番号>.md` (および組織の担当者ごとの `<人>.md`) — 電話帳・相手ごとの応答設定・緊急呼び出し許可
   - `通話記録/` — 通話ごとの文字起こし Markdown
   - `録音/` — ステレオ Ogg Opus 録音 (`録音/YYYY-MM/...ogg`)
   - `共有メモ/`・`受信箱/` — 後方支援 AI が参照する知識メモ
   - `設定/` — `情報の扱い.md`、`人格と態度.md` (無ければ `services/agent/prompts/` の既定が使われる)

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
  scripts/            デプロイ (VM / Cloud Run)・テスト通話・シナリオ試験・公開書き出しなど
services/
  agent/              応対 AI・hookd・calld・警備・時間割・メンバー表・既定のプロンプト
  directory-agent/    ワークスペースの番人 (下調べ・番号検索・録音 Ogg 変換・全文清書・要約)
  dashboard/          管制室 (Next.js)
```

## ライセンス

[GNU Affero General Public License v3.0](LICENSE)。
改変したものをネットワーク越しにサービスとして提供する場合も、その改変したソースを利用者に公開する必要があります。
