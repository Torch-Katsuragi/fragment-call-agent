# 管制室 (Web ダッシュボード)

フラグメントの Web UI (Next.js)。着信バナー、ライブの通話画面、履歴、設定、スマホアプリのペアリング。
スマホアプリも同じ API (`/api/device/*`) を端末トークンで叩く。

## 起動

```bash
cd services/dashboard
npm install
npm run dev              # 開発 (http://localhost:3000、ログインを省く)
npm run build && npm start   # 本番
```

## 設定

- 回線・DB・LiveKit などはリポジトリ直下の `.env` を読む (`lib/env.ts`)
- 認証まわりは `services/dashboard/.env.local` に置く:
  `AUTH_SECRET`、Google OAuth のクライアント (`AUTH_GOOGLE_ID` / `AUTH_GOOGLE_SECRET`)、
  `ALLOWED_EMAILS` (ログインを許すアカウント。カンマ区切り。**空なら誰も入れない**)、
  公開ホストで動かすときは `AUTH_URL`
- ワークスペース (電話帳など) の場所は `FRAGMENT_WORKSPACE`

## 画面と API のおもな対応

| 画面 | API |
|---|---|
| 着信バナー (切る / AI に任せる / 出る) | `/api/ringing`、`/api/pickup`、`/api/device/decide` |
| 通話画面 (文字起こし・吹き出し・耳打ち・交代・終話) | `/api/calls/[id]`、`/api/calls/[id]/whisper`、`/api/calls/[id]/hangup`、`/api/token` |
| 発信 | `/api/dial` |
| 設定 (応答モード・声・回線・端末) | `/api/settings`、`/api/device/pair` |
| 警備 | `/api/security` |
| スマホアプリ | `/api/device/state` (ロングポーリング)、`/api/device/config`、`/api/device/push` ほか |

## 注意

- 通話詳細の文字起こしは DB のポーリングが本体。LiveKit のテキストストリームは発話途中の表示と音声のモニタだけに使う
  (ストリームは入室後の発話しか拾えず、接続に失敗すると何も出ないため)
- 通話音声のモニタは、ブラウザの自動再生の制限があるので、ボタンを押してから始まる
