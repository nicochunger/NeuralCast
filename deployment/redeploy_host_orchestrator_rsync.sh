#!/usr/bin/env bash
set -euo pipefail

# Rsync-based deploy for the VPS host orchestrator package.
# Keeps the deployed code tree in sync (including deletions) while preserving
# generated local snippet media and Python cache artifacts.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

REMOTE_HOST="${REMOTE_HOST:-neuralvps}"
REMOTE_DIR="${REMOTE_DIR:-/root/radio_host_orchestrator}"
SSH_TARGET="${REMOTE_HOST}"

if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is required but not installed." >&2
  exit 1
fi

if ! command -v ssh >/dev/null 2>&1; then
  echo "ssh is required but not installed." >&2
  exit 1
fi

echo "[deploy] Target: ${SSH_TARGET}:${REMOTE_DIR}"
echo "[deploy] Repo:   ${REPO_ROOT}"

ssh "${SSH_TARGET}" "mkdir -p '${REMOTE_DIR}/src'"
ssh "${SSH_TARGET}" "mkdir -p '${REMOTE_DIR}/deployment/systemd'"
ssh "${SSH_TARGET}" "mkdir -p '${REMOTE_DIR}/deployment/cron'"

echo "[deploy] Syncing src/ (with delete + excludes; excluded files are preserved on VPS)..."
rsync -az --delete \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='*.mp3' \
  --exclude='neuralcast/assets/stories/snippets/' \
  "${REPO_ROOT}/src/" "${SSH_TARGET}:${REMOTE_DIR}/src/"

echo "[deploy] Syncing vps_requirements.txt..."
rsync -az \
  "${REPO_ROOT}/vps_requirements.txt" "${SSH_TARGET}:${REMOTE_DIR}/vps_requirements.txt"

echo "[deploy] Syncing deployment/..."
rsync -az --delete \
  "${REPO_ROOT}/deployment/" "${SSH_TARGET}:${REMOTE_DIR}/deployment/"

echo "[deploy] Removing legacy zip artifact (if present)..."
ssh "${SSH_TARGET}" "rm -f /root/deploy_host_orchestrator.zip"

echo "[verify] Key deployed entrypoints:"
ssh "${SSH_TARGET}" "ls -l \
  '${REMOTE_DIR}/src/neuralcast/cli/host_orchestrator.py' \
  '${REMOTE_DIR}/src/neuralcast/cli/schedule_generator.py' \
  '${REMOTE_DIR}/src/neuralcast/pipelines/host_orchestrator/main.py' \
  '${REMOTE_DIR}/src/neuralcast/pipelines/schedule_generator/main.py'"

echo "[verify] Legacy top-level pipeline files removed (expected: MISSING):"
ssh "${SSH_TARGET}" "for f in \
  '${REMOTE_DIR}/src/neuralcast/pipelines/host_orchestrator.py' \
  '${REMOTE_DIR}/src/neuralcast/pipelines/host_orchestrator_assets.py' \
  '${REMOTE_DIR}/src/neuralcast/pipelines/host_orchestrator_config.py' \
  '${REMOTE_DIR}/src/neuralcast/pipelines/host_orchestrator_generation.py' \
  '${REMOTE_DIR}/src/neuralcast/pipelines/host_orchestrator_models.py' \
  '${REMOTE_DIR}/src/neuralcast/pipelines/host_orchestrator_schedule.py' \
  '${REMOTE_DIR}/src/neuralcast/pipelines/host_orchestrator_state.py' \
  '${REMOTE_DIR}/src/neuralcast/pipelines/host_orchestrator_transport.py' \
  '${REMOTE_DIR}/src/neuralcast/pipelines/host_orchestrator_utils.py' \
  '${REMOTE_DIR}/src/neuralcast/pipelines/schedule_generator.py'; do \
    if [ -e \"\$f\" ]; then echo \"FOUND  \$f\"; else echo \"MISSING \$f\"; fi; \
  done"

echo "[deploy] Done."
