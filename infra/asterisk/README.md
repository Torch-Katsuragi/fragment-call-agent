# Asterisk (回線の仲介)

LiveKit SIP はトランク型 (INVITE を直接受ける) だけで、回線に REGISTER する機能を持たない
([livekit/sip#422](https://github.com/livekit/sip/issues/422) ほか)。
050 のように「こちらから事業者に REGISTER して着信を受ける」回線は、この Asterisk が仲介する。

回線の着信を受けて LiveKit SIP へ流すほか、着信の流れ (ダイヤルプラン) もここに書く:
警備の関門 → 取る → 録音告知 → 番号スクリーニング → 呼び出し (応答モードどおりに待つ) → LiveKit へ。
流れの全体は [DESIGN.md](../../DESIGN.md) の「着信の流れ」。

## ファイル

| ファイル | git | 役割 |
|---|---|---|
| `pjsip.conf.template` | 管理 | 回線・内線の定義のテンプレ (認証情報はプレースホルダ)。REGISTER 型と peer 型に対応 |
| `render_config.py` / `Render-Config.ps1` | 管理 | `.env` の値をテンプレに埋めて `pjsip.conf` を作る (同じ処理の Python 版と PowerShell 版。直すときは両方) |
| `pjsip.conf` | **除外** | 生成物 (SIP の認証情報を含む) |
| `extensions.conf` | 管理 | ダイヤルプラン |
| `rtp.conf` | 管理 | RTP のポート帯 (LiveKit と衝突させない) |
| `manager.conf` | 管理 | AMI (hookd からの発信に使う。ローカルとコンテナ網からだけ受ける) |
| `musiconhold.conf` + `moh/` | 管理 | 相手に聞かせる呼び出し音 (保留音の仕組みでループ)。音は `infra/scripts/render_ringback.py` で作る |
| `sounds/` | 管理 | 録音告知の音声。`infra/scripts/render_prompts.py` で作る |

## 使い方

```bash
# 1. 設定を生成 (リポジトリ直下で)
python3 infra/asterisk/render_config.py

# 2. 起動 (infra/ で)
docker compose --env-file ../.env --profile asterisk up -d

# 3. REGISTER の確認 (Registered になれば回線は通っている)
docker compose exec asterisk asterisk -rx "pjsip show registrations"
```

## ポート (host network を共有するので衝突させない)

| サービス | SIP | RTP |
|---|---|---|
| LiveKit server | 7880/7881 (WS/TCP) | 50000-50100/udp |
| LiveKit SIP | 5060/udp | 10000-10500/udp |
| Asterisk | **5070/udp** | 20000-20500/udp |

## 注意

- **同じ回線に REGISTER する別の SIP アプリがあると、着信を奪い合う。** 試験を別の環境で並行するときは、
  片方の Asterisk を止める (`docker compose --profile asterisk stop asterisk`)
- **通話が残ったまま Asterisk を再起動しない。** 先に `asterisk -rx "channel request hangup all"`。
  事業者側にセッションが残り、しばらく話し中になることがある
- 設定ファイルは単一ファイルのマウントなので、差し替えたら**コンテナを作り直す** (`dialplan reload` だけでは古いまま)
- NAT 越え (`external_*_address`) を設定するときは `local_net` もセットで。忘れると同じホスト内の SIP/RTP まで
  公開 IP へ回ってしまい、音が出ない
