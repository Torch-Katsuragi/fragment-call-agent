#!/usr/bin/env bash
# テスト通話を1本かけさせる (相手役AI caller-sim がダミー番号で 050 に架電する流れを模す)。
#
# 使い方 (手元の PC から):
#   infra/scripts/testcall.sh                 # 既定の筋書き (佐藤さんの食事の誘い)
#   infra/scripts/testcall.sh "折り返し希望の工務店。急ぎ"
#
# ⚠製品 (スマホアプリ・管制室) には置かない (2026-09-24 ユーザー「テスト通話がアプリ本体に
#   組み込まれてるのはおかしい。外部ツールであるべき」)。開発の道具は外に置く。
#   決まったシナリオを並べて回すのは run_scenarios.py (VM 上で実行)
# ⚠実電話は使わない (通話料はかからない。Gemini のトークンだけ)。
#   応答モードは実際の設定に従う — 不在なら AI がすぐ出る / スタンバイなら端末が鳴る
set -euo pipefail

PROJECT=your-gcp-project
ZONE=asia-northeast1-a
VM=call-agent

scenario="${1:-}"
q=$(python -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$scenario")

# hookd は VM の 127.0.0.1 でしか待ち受けていない (外に出す口は塞いである) ので ssh 越しに叩く
gcloud compute ssh "$VM" --project "$PROJECT" --zone "$ZONE" \
  --command "curl -s 'http://127.0.0.1:8790/simulate_call?scenario=$q'"
echo
