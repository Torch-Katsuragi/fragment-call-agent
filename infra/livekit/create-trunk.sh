#!/bin/sh
# LiveKit に inbound trunk + dispatch rule を登録する (M1: Asterisk→LiveKit ブリッジ用)
# 使い方 (WSL/Linux): sh infra/livekit/create-trunk.sh
# ※.env は CRLF の可能性があるため tr -d '\r' で読む
set -eu
cd "$(dirname "$0")/.."

envfile=../.env
export LIVEKIT_URL=ws://localhost:7880
export LIVEKIT_API_KEY=$(sed -n 's/^LIVEKIT_API_KEY=//p' "$envfile" | tr -d '\r')
export LIVEKIT_API_SECRET=$(sed -n 's/^LIVEKIT_API_SECRET=//p' "$envfile" | tr -d '\r')
[ -n "$LIVEKIT_API_KEY" ] || { echo "ERROR: LIVEKIT_API_KEY not found in .env"; exit 1; }
[ -n "$LIVEKIT_API_SECRET" ] || { echo "ERROR: LIVEKIT_API_SECRET not found in .env"; exit 1; }
echo "credentials loaded (key len $(printf %s "$LIVEKIT_API_KEY" | wc -c), secret len $(printf %s "$LIVEKIT_API_SECRET" | wc -c))"

run_lk() {
  docker run --rm --network host \
    -e LIVEKIT_URL -e LIVEKIT_API_KEY -e LIVEKIT_API_SECRET \
    -v "$PWD/livekit:/work" livekit/livekit-cli "$@"
}

run_lk sip inbound create /work/inbound-trunk.json
run_lk sip dispatch create /work/dispatch-rule.json
echo "=== inbound trunks ==="
run_lk sip inbound list
echo "=== dispatch rules ==="
run_lk sip dispatch list
