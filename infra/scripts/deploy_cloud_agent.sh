#!/usr/bin/env bash
# deploy_cloud_agent.sh — Cloud Run への Call-Agent 音声 AI (HTTP プッシュモード) デプロイ・切り替えスクリプト (2026-11-04)
#
# 使い方:
#   ./infra/scripts/deploy_cloud_agent.sh deploy [min0|min1]
#   ./infra/scripts/deploy_cloud_agent.sh switch [min0|min1]
#
# 前提:
#   - リージョン: asia-northeast1 (東京)
#   - Direct VPC Egress (--network=default --subnet=default --vpc-egress=private-ranges-only)
#     で GCE VM (10.0.0.x) の LiveKit / Postgres / hookd に内部 IP 直結 (外向き通信課金ゼロ)
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-your-gcp-project}"
REGION="${REGION:-asia-northeast1}"
SERVICE_NAME="${SERVICE_NAME:-call-agent-ai}"
AR_REPO="${AR_REPO:-call-agent}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${SERVICE_NAME}:latest"
VM_NAME="${VM_NAME:-call-agent}"
VM_ZONE="${VM_ZONE:-asia-northeast1-a}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-call-agent-vm@${PROJECT_ID}.iam.gserviceaccount.com}"

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-${REPO_DIR}/.env}"

ACTION="${1:-deploy}"
MODE="${2:-min0}"

get_vm_internal_ip() {
  gcloud compute instances describe "${VM_NAME}" \
    --project="${PROJECT_ID}" \
    --zone="${VM_ZONE}" \
    --format="value(networkInterfaces[0].networkIP)"
}

apply_mode_flags() {
  local mode="$1"
  if [[ "${mode}" == "min1" ]]; then
    echo "--min-instances=1 --max-instances=5 --cpu-throttling --cpu-boost"
  else
    echo "--min-instances=0 --max-instances=5 --cpu-throttling --cpu-boost"
  fi
}

build_env_yaml() {
  local vm_ip="$1"
  local out_yaml="$2"
  local tmp_vm_env
  tmp_vm_env="$(mktemp)"
  echo "==> Fetching production .env from VM (${VM_NAME}:~/call-agent-src/.env) ..."
  if ! gcloud compute ssh "${VM_NAME}" --project="${PROJECT_ID}" --zone="${VM_ZONE}" --command="cat ~/call-agent-src/.env" > "${tmp_vm_env}" 2>/dev/null || [[ ! -s "${tmp_vm_env}" ]]; then
    echo "WARN: VM .env fetch failed, falling back to local ${ENV_FILE}" >&2
    cp "${ENV_FILE}" "${tmp_vm_env}"
  fi
  local pybin="python"
  if command -v python3 >/dev/null 2>&1 && python3 -c "import sys" >/dev/null 2>&1; then
    pybin="python3"
  fi
  "${pybin}" - "${tmp_vm_env}" "${vm_ip}" "${out_yaml}" <<'PY'
import os, sys

env_path, vm_ip, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
vals = {}
if os.path.exists(env_path):
    with open(env_path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            vals[k] = v

pass_keys = [
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "GOOGLE_API_KEY",
    "VERTEX_PROJECT",
    "DEEPGRAM_API_KEY",
    "ELEVEN_API_KEY",
    "OWNER_NAME",
    "AI_CORE",
    "GEMINI_VOICE",
    "GEMINI_LIVE_MODEL",
    "GEMINI_TEXT_MODEL",
    "GEMINI_STT_REFINE_MODEL",
    "GEMINI_TTS_MODEL",
    "FIXED_LINES_VIA_MODEL",
    "LIVE_END_SILENCE_MS",
    "TTS_PROVIDER",
    "STT_PROVIDER",
    "GOOGLE_STT_MODEL",
    "GOOGLE_STT_LOCATION",
    "GOOGLE_STT_END_TIMEOUT",
    "GOOGLE_TTS_VOICE",
    "GOOGLE_TTS_STREAMING",
    "AIVIS_API_KEY",
    "AIVIS_MODEL_UUID",
    "AIVIS_SPEAKING_RATE",
    "AIVIS_VOLUME",
    "HOLD_TIMEOUT_SEC",
    "SECURITY_ENFORCE",
    "SECURITY_LOCAL_NUMBERS",
    "SECURITY_ALLOW_NUMBERS",
    "SECURITY_STRICT_CALLEE",
    "SECURITY_MIN_CALLER_DIGITS",
    "SECURITY_RATE_MAX",
    "SECURITY_RATE_WINDOW_SEC",
    "SECURITY_BLOCK_SEC",
]

out = {k: vals[k] for k in pass_keys if k in vals and vals[k] != ""}
out["AGENT_MODE"] = "http"
out["ENABLE_JSONL_BACKUP"] = "0"
out["LIVE_VAD"] = vals.get("LIVE_VAD") or "webrtc"
out["LIVEKIT_URL"] = f"ws://{vm_ip}:7880"
out["HOOKD_URL"] = f"http://{vm_ip}:8790"
out["DATABASE_URL"] = f"postgresql://callagent:callagent@{vm_ip}:5432/callagent"

with open(out_path, "w", encoding="utf-8") as f:
    for k, v in out.items():
        escaped = str(v).replace("\\", "\\\\").replace('"', '\\"')
        f.write(f'{k}: "{escaped}"\n')
PY
  rm -f "${tmp_vm_env}"
}

case "${ACTION}" in
  deploy)
    VM_INTERNAL_IP="$(get_vm_internal_ip)"
    echo "==> VM internal IP: ${VM_INTERNAL_IP}"
    echo "==> Building image ${IMAGE} ..."
    gcloud builds submit "${REPO_DIR}/services/agent" \
      --project="${PROJECT_ID}" \
      --tag="${IMAGE}"

    TMP_ENV_YAML="$(mktemp)"
    trap 'rm -f "${TMP_ENV_YAML}"' EXIT
    build_env_yaml "${VM_INTERNAL_IP}" "${TMP_ENV_YAML}"

    MODE_FLAGS="$(apply_mode_flags "${MODE}")"
    echo "==> Deploying ${SERVICE_NAME} (${MODE}: ${MODE_FLAGS}) ..."
    # shellcheck disable=SC2086
    gcloud run deploy "${SERVICE_NAME}" \
      --project="${PROJECT_ID}" \
      --region="${REGION}" \
      --image="${IMAGE}" \
      --service-account="${SERVICE_ACCOUNT}" \
      --cpu=1 \
      --memory=1Gi \
      --concurrency=2 \
      --timeout=3600 \
      --no-allow-unauthenticated \
      --network=default \
      --subnet=default \
      --vpc-egress=private-ranges-only \
      --env-vars-file="${TMP_ENV_YAML}" \
      ${MODE_FLAGS}

    echo "==> Granting roles/run.invoker and roles/speech.client to ${SERVICE_ACCOUNT} ..."
    gcloud run services add-iam-policy-binding "${SERVICE_NAME}" \
      --project="${PROJECT_ID}" \
      --region="${REGION}" \
      --member="serviceAccount:${SERVICE_ACCOUNT}" \
      --role="roles/run.invoker" >/dev/null
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${SERVICE_ACCOUNT}" \
      --role="roles/speech.client" --quiet >/dev/null

    SERVICE_URL="$(gcloud run services describe "${SERVICE_NAME}" --project="${PROJECT_ID}" --region="${REGION}" --format="value(status.url)")"
    echo "==> Deployed: ${SERVICE_URL}"
    ;;

  switch)
    VM_INTERNAL_IP="$(get_vm_internal_ip)"
    TMP_ENV_YAML="$(mktemp)"
    trap 'rm -f "${TMP_ENV_YAML}"' EXIT
    build_env_yaml "${VM_INTERNAL_IP}" "${TMP_ENV_YAML}"
    MODE_FLAGS="$(apply_mode_flags "${MODE}")"
    echo "==> Switching ${SERVICE_NAME} to ${MODE} (${MODE_FLAGS}) and syncing VM .env ..."
    # shellcheck disable=SC2086
    gcloud run services update "${SERVICE_NAME}" \
      --project="${PROJECT_ID}" \
      --region="${REGION}" \
      --concurrency=2 \
      --env-vars-file="${TMP_ENV_YAML}" \
      ${MODE_FLAGS}
    ;;

  *)
    echo "Usage: $0 {deploy|switch} {min0|min1}" >&2
    exit 1
    ;;
esac
