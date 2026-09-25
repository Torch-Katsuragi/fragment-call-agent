#!/usr/bin/env bash
# VMへのデプロイ (2026-07-31)。
#
# ⚠なぜ要るか — 手で運んでいた頃に2種類の事故を起こした:
#   ① 変更したファイルを1つ運び忘れて、VM上だけ中途半端な状態になる
#      → **常にツリー全体を送る** (git archive HEAD)。選ばないので忘れられない
#   ② Dockerの**単一ファイルbindマウント**は、ファイルが差し替わると
#      inodeが変わって追従しない。しかも `dialplan reload` は「成功」と答える
#      → 該当コンテナは**設定が変わったら必ず再起動**し、最後に**中身を突き合わせる**
#
# ⚠VMはgitリポジトリではない (GitHubの認証情報をVMに置かない方針)。
#   代わりに最後にデプロイしたコミットを ~/call-agent-src/.deployed_commit に置き、
#   そこからの差分で「何を再起動すべきか」を決める。
#
# 使い方:
#   infra/scripts/deploy.sh              # 差分から対象を自動判定
#   infra/scripts/deploy.sh agent        # 対象を明示 (dashboard/agent/asterisk/worker/all)
set -euo pipefail

PROJECT=your-gcp-project
ZONE=asia-northeast1-a
VM=call-agent
REMOTE_DIR='$HOME/call-agent-src'

cd "$(git rev-parse --show-toplevel)"
COMMIT=$(git rev-parse HEAD)

# ⚠先に索引を更新する。Windowsの改行コード正規化(autocrlf)で、中身が同じでも
#   stat情報のズレだけで「変更あり」に見えることがある (実際に誤爆した)
git update-index -q --refresh || true
if [ -n "$(git status --porcelain)" ]; then
  echo "⚠コミットされていない変更があります。git archive はHEADを送るので反映されません:" >&2
  git status --short >&2
  echo "--- 続けますか? (HEADの内容が送られます) [y/N]" >&2
  read -r ans
  [ "$ans" = "y" ] || exit 1
fi

ssh_vm() { gcloud compute ssh "$VM" --project "$PROJECT" --zone "$ZONE" --command "$1"; }

# --- 前回デプロイしたコミットを取得して、変更されたパスを出す ---
# ⚠「前回が不明」と「変更なし」を同じ空文字で表すと、変更が無いのに全体を
#   再起動してしまう (最初の版で踏んだ)。前者は FULL=1 で区別する。
PREV=$(ssh_vm "cat $REMOTE_DIR/.deployed_commit 2>/dev/null || true" | tr -d '\r\n ')
FULL=0
CHANGED=""
DELETED=""
if [ -n "$PREV" ] && git cat-file -e "$PREV^{commit}" 2>/dev/null; then
  CHANGED=$(git diff --name-only "$PREV" "$COMMIT")
  # ⚠消したファイルは tar 展開では消えない。前回から消えた分を VM でも消す (2026-09-24、撤去した
  #   /api/devices と /api/testcall が VM に残って動き続けていた)
  DELETED=$(git diff --name-only --diff-filter=D "$PREV" "$COMMIT" | tr '\n' ' ')
  echo "前回: $PREV → 今回: $COMMIT"
else
  FULL=1
  echo "前回のコミットが不明なので全体を対象にします"
fi

# --- 対象の決定 ---
targets="${*:-}"
if [ -z "$targets" ]; then
  if [ "$FULL" = "1" ]; then
    targets="all"
  else
    case "$CHANGED" in *services/dashboard/*) targets="$targets dashboard";; esac
    case "$CHANGED" in *services/agent/*|*infra/docker-compose.yml*) targets="$targets agent";; esac
    # ⚠hookd.py と security.py は services/agent/ に置かれているが、動いているのは
    #   別コンテナ (infra-hookd-1)。agent だけ再起動して hookd が古いまま、を実際に踏んだ
    case "$CHANGED" in *services/agent/hookd.py*|*services/agent/security.py*|*infra/docker-compose.yml*) targets="$targets hookd";; esac
    case "$CHANGED" in *infra/asterisk/*) targets="$targets asterisk";; esac
    case "$CHANGED" in *services/directory-agent/*) targets="$targets worker";; esac
  fi
fi
# ⚠ `|| true` が要る: 対象が空だと grep がマッチ0件で終了コード1を返し、
#   set -e のもとでは代入ごと失敗してスクリプトが黙って止まる (実際に踏んだ)
targets=$(echo "$targets" | tr ' ' '\n' | grep -v '^$' | sort -u | tr '\n' ' ' || true)
[ -n "$targets" ] || echo "再起動が要る変更はありません (ファイルだけ更新します)"
echo "対象: ${targets:-(なし)}"

# --- ツリー全体を送る ---
TAR=$(mktemp -t deploy-XXXXXX.tar)
git archive HEAD -o "$TAR"
gcloud compute scp "$TAR" "$VM:/tmp/deploy.tar" --project "$PROJECT" --zone "$ZONE" >/dev/null
rm -f "$TAR"

# --- 展開 + 再起動 + 検証 ---
# ⚠ .env / infra/secrets / data / node_modules はgit管理外なので tar 展開で消えない
ssh_vm "
set -e
cd $REMOTE_DIR
tar xf /tmp/deploy.tar
for f in ${DELETED:-}; do rm -f -- \"\$f\" && echo \"  消去: \$f\"; done
echo '$COMMIT' > .deployed_commit

t=' $targets '
case \"\$t\" in *' all '*) t=' dashboard agent hookd asterisk worker ';; esac

if [ \"\$t\" != \"\${t/ asterisk /}\" ]; then
  echo '--- asterisk: 設定は単一ファイルbindマウントなので必ず再起動する ---'
  active=\$(sudo docker exec infra-asterisk-1 asterisk -rx 'core show channels' | grep -c 'active call' || true)
  sudo docker exec infra-asterisk-1 asterisk -rx 'core show channels' | tail -2
  # ⚠ docker restart ではなく compose で作り直す (2026-09-18)。restart は古い environment を
  #   引きずるので、docker-compose.yml に足した環境変数 (STANDBY_RING_SEC) がコンテナに入らず、
  #   ダイヤルプランが空の秒数で走った。作り直しなら env も bind も新しくなる
  (cd infra && sudo docker compose --env-file ../.env --profile asterisk --profile vm-worker --profile testcall up -d --force-recreate asterisk 2>&1 | tail -1)
  sleep 10
  # ⚠マウント越しに中身が本当に入れ替わったかを突き合わせる (今日ここで騙された)
  for f in extensions.conf rtp.conf musiconhold.conf; do
    h=\$(md5sum infra/asterisk/\$f | cut -d' ' -f1)
    c=\$(sudo docker exec infra-asterisk-1 md5sum /etc/asterisk/\$f | cut -d' ' -f1)
    [ \"\$h\" = \"\$c\" ] && echo \"  OK \$f\" || { echo \"  ✗ \$f がコンテナに反映されていない\"; exit 1; }
  done
fi

svc=''
[ \"\$t\" != \"\${t/ agent /}\" ] && svc=\"\$svc agent\"
[ \"\$t\" != \"\${t/ hookd /}\" ] && svc=\"\$svc hookd\"
[ \"\$t\" != \"\${t/ worker /}\" ] && svc=\"\$svc directory-agent\"
if [ -n \"\$svc\" ]; then
  echo \"--- 再ビルド:\$svc ---\"
  (cd infra && sudo docker compose --env-file ../.env --profile asterisk --profile vm-worker --profile testcall up -d --build \$svc 2>&1 | tail -3)
  sleep 8
  # ⚠コンテナ名とファイルの対応。hookd.py は services/agent/ にあるが別コンテナで動く
  [ \"\$t\" != \"\${t/ agent /}\" ] && check=\"\$check infra-agent-1:/app/agent.py:services/agent/agent.py\"
  [ \"\$t\" != \"\${t/ hookd /}\" ] && check=\"\$check infra-hookd-1:/app/hookd.py:services/agent/hookd.py\"
  for pair in \$check; do
    cont=\${pair%%:*}; rest=\${pair#*:}; cpath=\${rest%%:*}; hpath=\${rest#*:}
    h=\$(md5sum \$hpath | cut -d' ' -f1)
    c=\$(sudo docker exec \$cont md5sum \$cpath | cut -d' ' -f1)
    [ \"\$h\" = \"\$c\" ] && echo \"  OK \$hpath\" || { echo \"  ✗ \$hpath が \$cont に反映されていない\"; exit 1; }
  done
fi

if [ \"\$t\" != \"\${t/ dashboard /}\" ]; then
  echo '--- dashboard: 依存 + ビルド + 再起動 ---'
  # ⚠install を忘れると package.json に依存を足したデプロイでビルドが壊れる
  #   (qrcode 追加 2026-08-01 が最初の該当)。依存に変化が無ければ一瞬で終わる
  (cd services/dashboard && npm install --no-audit --no-fund 2>&1 | tail -1 && npm run build 2>&1 | tail -3)
  sudo systemctl restart call-agent-dashboard
  sleep 4
  systemctl is-active call-agent-dashboard
fi

echo '--- 稼働確認 ---'
sudo docker ps --format '{{.Names}}\t{{.Status}}' | sort
"
echo "デプロイ完了: $COMMIT"
