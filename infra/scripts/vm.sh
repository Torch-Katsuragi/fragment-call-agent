#!/usr/bin/env bash
# VMの開け閉め (2026-09-17)。
#
# ⚠なぜスクリプトにするか —
#   2026-08-01 19:57 に「いつもどおり終業で stop」して、そのまま**47日間 050 が不通**だった。
#   落ちたのではなく、開け直さなかっただけ。監査ログを見るまで「VMがTERMINATED」=故障だと
#   誤解していた。**停止は事故に見えない**ので、開けたつもり/閉めたつもりを潰す。
#
#   up は「起動した」で終わらず、**電話が鳴る状態か**まで確かめる:
#     8コンテナ / ブラステルRegistered / tailnet到達 / 内線のendpoint
#   down は通話中でないことを確かめてから止める。
#
# 使い方:
#   infra/scripts/vm.sh up       # 開ける (確認込み)
#   infra/scripts/vm.sh down     # 閉じる (通話中なら止めない)
#   infra/scripts/vm.sh status   # 今どうなっているか
set -euo pipefail

PROJECT=your-gcp-project
ZONE=asia-northeast1-a
VM=call-agent

ssh_vm() { gcloud compute ssh "$VM" --project "$PROJECT" --zone "$ZONE" --command "$1" 2>/dev/null; }
vm_state() {
  gcloud compute instances describe "$VM" --project "$PROJECT" --zone "$ZONE" \
    --format="value(status)" 2>/dev/null
}

case "${1:-status}" in

up)
  state=$(vm_state)
  if [ "$state" = "RUNNING" ]; then
    echo "すでに RUNNING"
  else
    echo "起動中 ($state → RUNNING)…"
    gcloud compute instances start "$VM" --project "$PROJECT" --zone "$ZONE" >/dev/null
  fi

  # sshd が上がるまで待つ (起動直後は Connection refused になる)
  echo -n "sshd 待ち"
  for _ in $(seq 1 30); do
    if ssh_vm "true" >/dev/null 2>&1; then echo " OK"; break; fi
    echo -n "."; sleep 5
  done

  echo "--- 確認 ---"
  ssh_vm "
    n=\$(docker ps --format '{{.Names}}' | grep -c '^infra-' || true)
    echo \"コンテナ: \$n / 8\"
    [ \"\$n\" = 8 ] || docker ps --format '  {{.Names}}\t{{.Status}}'

    reg=\$(docker exec infra-asterisk-1 asterisk -rx 'pjsip show registrations' | grep -c Registered || true)
    echo \"ブラステル: \$([ \"\$reg\" -ge 1 ] && echo Registered || echo '⚠未登録 — 050は鳴らない')\"

    echo \"tailnet: \$(tailscale ip -4 2>/dev/null || echo '⚠未接続 — 内線が繋がらない')\"

    echo '内線:'
    docker exec infra-asterisk-1 asterisk -rx 'pjsip show endpoints' \
      | grep -E '^ *Endpoint: *100[0-9]' | sed 's/^/  /'
    echo \"内線を鳴らす秒数: \$(docker exec infra-asterisk-1 printenv PHONES_RING_SEC 2>/dev/null || echo 0)\"
  "
  echo
  echo "⚠応答モード (AIが出るかどうか) は管制室のトグル。ここでは変えていない"
  ;;

down)
  state=$(vm_state)
  if [ "$state" != "RUNNING" ]; then
    echo "すでに $state — 何もしない"
    exit 0
  fi

  # ⚠通話中に止めると相手が切られる。ブラステル側にセッションが残って
  #   数分「話し中」になる実績もある (2026-07 のM1で踏んだ)
  calls=$(ssh_vm "docker exec infra-asterisk-1 asterisk -rx 'core show channels' | grep -oE '^[0-9]+ active call' | grep -oE '^[0-9]+'" | tr -d '\r\n ')
  if [ "${calls:-0}" != "0" ]; then
    echo "⚠通話中 (${calls}件) なので止めない。切れてからもう一度"
    exit 1
  fi

  echo "停止中…"
  gcloud compute instances stop "$VM" --project "$PROJECT" --zone "$ZONE" >/dev/null
  cat <<'EOS'
停止した。

⚠**この状態では050は鳴らず、着信の記録もこちらに残らない**
  (相手には呼び出し音が鳴り続けるか、ブラステルのアプリが受ける)。
  人に教えている番号なので、開け直しを忘れないこと → infra/scripts/vm.sh up

残る課金: ディスク50GB (約¥270/月) + 未使用の静的IP (約$7.3/月)。
静的IP 203.0.113.10 は Twilio の Origination URI に登録済みなので**解放しないこと**。
EOS
  ;;

status)
  echo "VM: $(vm_state)"
  if [ "$(vm_state)" = "RUNNING" ]; then
    ssh_vm "
      echo \"コンテナ: \$(docker ps --format '{{.Names}}' | grep -c '^infra-' || true) / 8\"
      docker exec infra-asterisk-1 asterisk -rx 'pjsip show registrations' | grep -E 'brastel' || true
      echo \"tailnet: \$(tailscale ip -4 2>/dev/null || echo 未接続)\"
      docker exec infra-asterisk-1 asterisk -rx 'pjsip show endpoints' | grep -E '^ *Endpoint: *100[0-9]' || true
    "
  fi
  ;;

*)
  echo "usage: $0 {up|down|status}" >&2
  exit 1
  ;;
esac
