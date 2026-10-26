#!/usr/bin/env bash
#
# Claude Code's PostToolUse hook for file edits (.claude/settings.json): when
# the edited file is markdown in this repo, run normalize-markdown.sh --fix on
# it. What markdownlint-cli2 still reports goes back to Claude, through exit
# status 2; any other file passes untouched.

set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." &>/dev/null && pwd -P)"
file="$(jq -r '.tool_input.file_path // empty')"

[[ "${file}" == *.md ]] || exit 0
# Physical paths on both sides, as normalize-markdown.sh compares them.
file="$(realpath -m -- "${file}")"
[[ "${file}" == "${root}"/* ]] || exit 0

normalize="${root}/scripts/normalize-markdown.sh"
if ! out="$("${normalize}" --fix "${file}" 2>&1)"; then
  printf '%s\n' "${out}" >&2
  exit 2
fi
