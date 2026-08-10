#!/usr/bin/env bash
set -euo pipefail

NETWORK_NAME="${AZURACAST_DOCKER_NETWORK:-azuracast_default}"
CONTAINER_NAME="${AZURACAST_WEB_CONTAINER:-azuracast}"
APP_ROOT="${APP_ROOT:-/root/projects/NeuralCast}"
ENV_FILE="${ENV_FILE:-${APP_ROOT}/.env}"
ADMIN_PORT="${NEURALCAST_ADMIN_HTTP_PORT:-8787}"
ADMIN_BIND_HOST="${NEURALCAST_ADMIN_HTTP_HOST:-172.18.0.1}"
PUBLIC_HEALTH_URL="${NEURALCAST_ADMIN_HTTP_PUBLIC_HEALTH_URL:-https://neuralcast.duckdns.org/admin-http/healthz}"
MAX_WAIT_SECONDS="${MAX_WAIT_SECONDS:-1800}"
SLEEP_SECONDS="${SLEEP_SECONDS:-15}"

log() {
  printf '[admin-api-bridge-repair] %s %s\n' "$(date -u +%FT%TZ)" "$*"
}

require_command() {
  local command_name="$1"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    log "Missing required command: ${command_name}"
    exit 1
  fi
}

wait_for_ready() {
  local description="$1"
  shift

  local elapsed=0
  while ! "$@"; do
    elapsed=$((elapsed + SLEEP_SECONDS))
    if [ "${elapsed}" -ge "${MAX_WAIT_SECONDS}" ]; then
      log "Timed out waiting for ${description} after ${MAX_WAIT_SECONDS}s"
      return 1
    fi
    sleep "${SLEEP_SECONDS}"
  done
}

load_env_file() {
  if [ -f "${ENV_FILE}" ]; then
    set -a
    # shellcheck disable=SC1090
    . "${ENV_FILE}"
    set +a
  else
    log "Environment file ${ENV_FILE} not found; continuing with current environment"
  fi
}

network_ready() {
  docker network inspect "${NETWORK_NAME}" >/dev/null 2>&1
}

container_ready() {
  docker inspect "${CONTAINER_NAME}" >/dev/null 2>&1
}

bridge_exists() {
  local bridge_name="$1"
  ip link show "${bridge_name}" >/dev/null 2>&1
}

delete_stale_ufw_rules() {
  local current_bridge="$1"
  local stale_numbers=()
  local line
  local number
  local bridge_name

  while IFS= read -r line; do
    case "${line}" in
      *"${ADMIN_PORT}/tcp on br-"*"ALLOW IN"*)
        number="$(printf '%s\n' "${line}" | sed -n 's/^\[[[:space:]]*\([0-9][0-9]*\)\].*/\1/p')"
        bridge_name="$(printf '%s\n' "${line}" | sed -n "s/^.*${ADMIN_PORT}\\/tcp on \\([^[:space:]]*\\)[[:space:]].*$/\\1/p")"
        if [ -n "${number}" ] && [ -n "${bridge_name}" ] && [ "${bridge_name}" != "${current_bridge}" ]; then
          stale_numbers+=("${number}")
        fi
        ;;
    esac
  done < <(ufw status numbered)

  local index
  for ((index=${#stale_numbers[@]} - 1; index>=0; index--)); do
    log "Deleting stale UFW rule #${stale_numbers[index]}"
    ufw --force delete "${stale_numbers[index]}" >/dev/null
  done
}

ensure_current_ufw_rule() {
  local bridge_name="$1"
  local subnet="$2"

  if ufw status verbose | grep -Fq "${ADMIN_PORT}/tcp on ${bridge_name} ALLOW IN    ${subnet}"; then
    log "UFW already allows port ${ADMIN_PORT} on ${bridge_name} from ${subnet}"
    return
  fi

  log "Adding UFW allow rule for port ${ADMIN_PORT} on ${bridge_name} from ${subnet}"
  ufw allow in on "${bridge_name}" from "${subnet}" to any port "${ADMIN_PORT}" proto tcp >/dev/null
}

check_container_backend_health() {
  docker exec "${CONTAINER_NAME}" curl -fsS --max-time 15 "http://${ADMIN_BIND_HOST}:${ADMIN_PORT}/healthz" >/dev/null
}

check_container_proxy_health() {
  docker exec "${CONTAINER_NAME}" curl -fsS --max-time 15 "http://localhost/admin-http/healthz" >/dev/null
}

check_container_capabilities() {
  if [ -z "${NEURALCAST_ADMIN_HTTP_TOKEN:-}" ]; then
    log "NEURALCAST_ADMIN_HTTP_TOKEN is not available; skipping authenticated capabilities check"
    return 0
  fi

  docker exec \
    -e NC_ADMIN_HTTP_TOKEN="${NEURALCAST_ADMIN_HTTP_TOKEN}" \
    "${CONTAINER_NAME}" \
    sh -lc 'curl -fsS --max-time 15 -H "Authorization: Bearer ${NC_ADMIN_HTTP_TOKEN}" http://localhost/admin-http/admin/capabilities >/dev/null'
}

check_public_health() {
  curl -fsS --max-time 20 "${PUBLIC_HEALTH_URL}" >/dev/null
}

main() {
  if [ "$(id -u)" -ne 0 ]; then
    log "This script must run as root"
    exit 1
  fi

  require_command docker
  require_command curl
  require_command ufw
  require_command ip
  require_command awk

  load_env_file

  log "Waiting for Docker network ${NETWORK_NAME}"
  wait_for_ready "Docker network ${NETWORK_NAME}" network_ready

  log "Waiting for container ${CONTAINER_NAME}"
  wait_for_ready "container ${CONTAINER_NAME}" container_ready

  local network_id
  local subnet
  local bridge_name
  network_id="$(docker network inspect -f '{{.Id}}' "${NETWORK_NAME}")"
  subnet="$(docker network inspect -f '{{(index .IPAM.Config 0).Subnet}}' "${NETWORK_NAME}")"
  bridge_name="br-${network_id:0:12}"

  log "Current ${NETWORK_NAME} bridge is ${bridge_name} (${subnet})"
  wait_for_ready "bridge interface ${bridge_name}" bridge_exists "${bridge_name}"

  if ufw status | grep -q '^Status: active'; then
    delete_stale_ufw_rules "${bridge_name}"
    ensure_current_ufw_rule "${bridge_name}" "${subnet}"
  else
    log "UFW is inactive; skipping firewall rule maintenance"
  fi

  log "Checking direct container-to-admin-api health"
  wait_for_ready "container backend health" check_container_backend_health

  log "Checking nginx proxy health inside the AzuraCast container"
  wait_for_ready "container proxy health" check_container_proxy_health

  log "Checking authenticated capabilities route through nginx"
  wait_for_ready "authenticated capabilities route" check_container_capabilities

  if [ -n "${PUBLIC_HEALTH_URL}" ]; then
    log "Checking public admin API health at ${PUBLIC_HEALTH_URL}"
    wait_for_ready "public admin API health" check_public_health
  fi

  log "Admin API bridge repair completed successfully"
}

main "$@"
