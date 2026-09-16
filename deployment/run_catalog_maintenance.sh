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

publish_catalog_changes() {
    local branch
    branch=$(git symbolic-ref --quiet --short HEAD) || return 1
    if [[ "${branch}" != "main" ]]; then
        log "Refusing automatic catalog publication from branch ${branch}; expected main"
        return 1
    fi

    # Enumerate only catalog sources, including deletions and new CSVs. Never
    # sweep up media, runtime state, logs, or unrelated staged development work.
    local -a paths=()
    local inventory
    inventory=$(mktemp) || return 1
    if ! git ls-files -z --cached --others --exclude-standard -- \
        ':(glob)NeuralCast/playlists/*.csv' \
        ':(glob)NeuralForge/playlists/*.csv' \
        'NeuralCast/metadata/ArtistIDs.json' \
        'NeuralCast/metadata/New Releases.metadata.json' \
        'NeuralCast/metadata/New Releases.exclusions.json' \
        'NeuralForge/metadata/ArtistIDs.json' \
        'NeuralForge/metadata/New Releases.metadata.json' \
        'NeuralForge/metadata/New Releases.exclusions.json' > "${inventory}"; then
        rm -f "${inventory}"
        return 1
    fi
    mapfile -d '' -t paths < "${inventory}"
    rm -f "${inventory}"
    if (( ${#paths[@]} )); then
        git add -A -- "${paths[@]}" || return 1
        if git diff --cached --quiet -- "${paths[@]}"; then
            log "No catalog changes to commit"
        else
            git commit --only -m "Update station catalogs after scheduled maintenance" \
                -- "${paths[@]}" || return 1
        fi
    fi

    # Always retry a previous unpushed commit, even on an otherwise unchanged
    # run. Never force-push, auto-merge, or discard work after a remote rejection.
    GIT_TERMINAL_PROMPT=0 git push origin main || return 1
    log "Catalog changes published to origin/main"
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

if (( status == 0 )); then
    if ! publish_catalog_changes; then
        log "FAILED catalog commit/push; changes retained locally for the next successful run"
        status=1
    fi
else
    log "Skipping catalog commit/push because a pipeline failed"
fi

log "Catalog maintenance finished with status ${status}"
exit "${status}"
