#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="${1:-$(cd -- "${script_dir}/.." && pwd)}"
group_name="${CMUX_GROUP_NAME:-amber}"
codex_command="${CMUX_CODEX_COMMAND:-codex -a on-request -s workspace-write}"
temporary_group_name="${group_name}-recreate-$$-${RANDOM}"

for command in cmux jq; do
    if ! command -v "${command}" >/dev/null 2>&1; then
        echo "Required command not found: ${command}" >&2
        exit 1
    fi
done

if [[ ! -d "${project_dir}" ]]; then
    echo "Project directory does not exist: ${project_dir}" >&2
    exit 1
fi

if ! cmux ping >/dev/null; then
    echo "cmux is not running or its Unix socket is unavailable." >&2
    exit 1
fi

# A temporary unique name lets us discover the new refs without relying on
# workspace/group indexes, which change as other cmux workspaces are opened.
cmux workspace-group create \
    --name "${temporary_group_name}" \
    --cwd "${project_dir}" >/dev/null

group_json="$(cmux workspace-group list --json)"
group_ref="$(
    jq -er --arg name "${temporary_group_name}" \
        '.groups[] | select(.name == $name) | .ref' <<<"${group_json}"
)"
workspace_ref="$(
    jq -er --arg name "${temporary_group_name}" \
        '.groups[] | select(.name == $name) | .anchor_workspace_ref' <<<"${group_json}"
)"

cmux workspace-group rename "${group_ref}" --name "${group_name}"
cmux workspace-group set-color "${group_ref}" --hex '#FFBF00'
cmux workspace-group set-icon "${group_ref}" --symbol 'hexagon.fill'
cmux workspace-group unpin "${group_ref}"
cmux workspace-group expand "${group_ref}"

# The anchor starts with one terminal. Launch Codex there, then create the
# matching idle terminal as an equally sized split on its right.
cmux send --workspace "${workspace_ref}" "${codex_command}"
cmux send-key --workspace "${workspace_ref}" Enter
cmux new-pane \
    --type terminal \
    --direction right \
    --workspace "${workspace_ref}" \
    --focus false >/dev/null

cmux workspace-group focus "${group_ref}"

echo "Recreated cmux group ${group_name}: ${group_ref} (${workspace_ref})"
