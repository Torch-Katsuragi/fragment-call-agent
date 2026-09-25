#!/usr/bin/env bash
# エージェントだけ手元 (このPC) で回す開発モード (2026-09-18)。
#
# なぜ要るか: 挙動の確認のたびに VM へデプロイ (archive → scp → docker build → 再起動) すると
#   1回 2〜3 分かかる。ユーザーの指摘「挙動の確認くらいならローカルで回して2体制で」。
#   LiveKit / hookd / Postgres / Asterisk / 相手役AI は VM のまま、**agent プロセスだけ**を
#   このPCの venv で動かす。ファイルを保存すると livekit-agents の dev モードが自動で読み直す
#   ので、編集 → 数秒 → 次の通話で反映。
#
# 仕組み:
#   ・LiveKit へは公開の wss://livekit.example.com で参加 (メディアは VM の公開 UDP)
#   ・hookd と Postgres は VM の 127.0.0.1 にしか無いので ssh トンネルで引く
#   ・⚠VM 側の agent コンテナは**止める** (agent_name 無しの自動ディスパッチなので、
#     2つ動くと通話が交互に割り振られて「直したはずが直っていない」になる)。
#     終了時 (Ctrl-C) に必ず戻す
#   ・run_scenarios.py は今までどおり VM 上で回す (相手役AIと hookd はあちらにいる)
#
# 使い方 (Git Bash、リポジトリ直下から):
#   infra/scripts/dev_agent.sh
#   別ターミナルで:  gcloud compute ssh call-agent --project your-gcp-project --zone asia-northeast1-a \
#                     --command 'cd ~/call-agent-src && python3 infra/scripts/run_scenarios.py aizuchi'
#   本番の agent に戻すときは Ctrl-C (trap で VM の agent を start する)
#   ⚠taskkill 等で外から殺すと trap が走らず VM の agent が止まったままになる。その場合は
#     gcloud compute ssh call-agent --project your-gcp-project --zone asia-northeast1-a --command 'sudo docker start infra-agent-1'
#   2026-09-18 に検証済み: 手元の agent で aizuchi シナリオ PASS (相手役と hookd は VM)
set -euo pipefail

PROJECT=your-gcp-project
ZONE=asia-northeast1-a
VM=call-agent
cd "$(git rev-parse --show-toplevel)"

ssh_vm() { gcloud compute ssh "$VM" --project "$PROJECT" --zone "$ZONE" --command "$1"; }

# --- 手元の venv ---
PY="$PWD/services/agent/.venv/Scripts/python"   # ⚠絶対パス。最後に cd services/agent するので相対だと壊れる
[ -x "$PY" ] || { echo "venv が無い: python -m venv services/agent/.venv && .venv/Scripts/pip install -r services/agent/requirements.txt" >&2; exit 1; }

# --- トンネル (hookd 8790 / postgres 5432→55432。⚠5432 は手元の別 Postgres と衝突しうる) ---
gcloud compute ssh "$VM" --project "$PROJECT" --zone "$ZONE" -- -N \
  -L 8790:127.0.0.1:8790 -L 55432:127.0.0.1:5432 &
TUNNEL=$!

restore() {
  echo; echo "--- VM の agent を戻す ---"
  ssh_vm "sudo docker start infra-agent-1 >/dev/null && sudo docker ps --format '{{.Names}} {{.Status}}' | grep agent-1" || true
  kill "$TUNNEL" 2>/dev/null || true
}
trap restore EXIT

echo "--- VM の agent を止める (手元が代わりに受ける) ---"
ssh_vm "sudo docker stop infra-agent-1 >/dev/null; echo stopped"

# --- 環境: VM の .env の写し (.env.vm) を土台に、手元向けの差分を上書き ---
# ⚠手元の .env は鍵が VM と**別物** (LiveKit の API キーも Gemini のキーも違う。2026-09-18 に
#   md5 で確認)。VM の LiveKit に参加するには VM 側の鍵が要るので、写しを使う:
#     gcloud compute scp call-agent:~/call-agent-src/.env ./.env.vm --project your-gcp-project --zone asia-northeast1-a
#   .env.vm は .gitignore 済み
[ -f .env.vm ] || { echo ".env.vm が無い (上のコメントの scp で取る)" >&2; exit 1; }
# ⚠ `. .env.vm` は使わない。値に空白や記号を含む行 (Twilio の URI など) があると bash が
#   コマンドとして解釈する (実際に "262: command not found" で落ちた)。1行ずつ KEY=VALUE で export
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in ''|'#'*) continue;; esac
  key=${line%%=*}; val=${line#*=}
  [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
  export "$key=$val"
done < .env.vm
export LIVEKIT_URL="${LIVEKIT_PUBLIC_URL:-wss://livekit.example.com}"
export HOOKD_URL=http://127.0.0.1:8790
export DATABASE_URL=postgresql://callagent:callagent@127.0.0.1:55432/callagent
export TRANSCRIPT_DIR="$PWD/data/transcripts"
export FRAGMENT_WORKSPACE="${FRAGMENT_WORKSPACE:-./workspace}"
export GOOGLE_TTS_CREDENTIALS="$PWD/infra/secrets/callagent-tts.json"
export AI_CORE="${AI_CORE:-live}"
mkdir -p "$TRANSCRIPT_DIR"

# Windows のコンソールは cp932 なので、ログの "—" 等で UnicodeEncodeError が出る (実測)。UTF-8 に固定
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8

echo "--- agent を dev モードで起動 (保存すると自動で読み直す) ---"
echo "    AI_CORE=$AI_CORE  LIVEKIT_URL=$LIVEKIT_URL  LIVE_END_SILENCE_MS=${LIVE_END_SILENCE_MS:-800}"
cd services/agent
exec "$PY" agent.py dev
