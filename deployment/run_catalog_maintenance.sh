#!/usr/bin/env bash

set -uo pipefail

project_root="${NC_MAINTENANCE_PROJECT_ROOT:-/root/projects/NeuralCast}"
python_bin="${NC_MAINTENANCE_PYTHON:-${project_root}/.venv/bin/python}"
lock_file="${NC_MAINTENANCE_LOCK_FILE:-${project_root}/runtime/catalog-maintenance.lock}"
mode="${1:-}"

log() {
    printf '[%s] %s\n' "$(date --iso-8601=seconds)" "$*"
}

run_pipeline() {
    local label="$1"
    shift

    log "Starting ${label}"
    if "${python_bin}" "$@"; then
        log "Completed ${label}"
        return 0
    else
        local exit_code=$?
        log "FAILED ${label} (exit ${exit_code})"
        return "${exit_code}"
    fi
}

if [[ "${mode}" != "daily" && "${mode}" != "saturday" ]]; then
    echo "Usage: $0 {daily|saturday}" >&2
    exit 2
fi

if [[ ! -x "${python_bin}" ]]; then
    echo "Python executable is unavailable: ${python_bin}" >&2
    exit 2
fi

mkdir -p "$(dirname "${lock_file}")"
exec 9>"${lock_file}"
if ! flock -n 9; then
    log "Another catalog maintenance run is active; skipping ${mode} run"
    exit 0
fi

cd "${project_root}" || exit 2
export PYTHONPATH="${project_root}/src${PYTHONPATH:+:${PYTHONPATH}}"

status=0

if [[ "${mode}" == "saturday" ]]; then
    if run_pipeline \
        "NeuralForge New Releases" \
        -m neuralcast.cli.update_new_releases -s neuralforge; then
        run_pipeline \
            "NeuralForge playlist sync" \
            -m neuralcast.cli.sync_playlists -s neuralforge || status=1
    else
        log "Skipping NeuralForge playlist sync because New Releases failed"
        status=1
    fi
else
    run_pipeline \
        "NeuralForge playlist sync" \
        -m neuralcast.cli.sync_playlists -s neuralforge || status=1
fi

run_pipeline \
    "NeuralCast playlist sync" \
    -m neuralcast.cli.sync_playlists -s neuralcast || status=1

log "Catalog maintenance finished with status ${status}"
exit "${status}"
