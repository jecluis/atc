#!/usr/bin/env bash
# A status line for Claude Code that also records the account's rate limits,
# where `atc usage` and `atc run -a auto` read them. Claude Code passes the
# session as JSON on standard input; atc sets ATC_ACCOUNT and ATC_STATE_DIR.

input=$(cat)

field() {
  echo "${input}" | jq -r "$1 // empty"
}

# Written to a temporary file and renamed, so atc never reads half a record.
record_usage() {
  local account="${ATC_ACCOUNT:-}" state="${ATC_STATE_DIR:-}"
  [[ "${account}" =~ ^[a-z0-9][a-z0-9_-]*$ && "${state}" == /* ]] || return 0
  local record
  record=$(echo "${input}" | jq -c --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    'select(.rate_limits | type == "object")
     | {recorded_at: $at, rate_limits: .rate_limits}' 2>/dev/null)
  [[ -n "${record}" ]] || return 0

  local dir="${state}/usage" tmp
  mkdir -m 700 "${state}" "${dir}" 2>/dev/null
  [[ -d "${dir}" ]] || return 0
  tmp=$(mktemp "${dir}/.${account}.XXXXXX" 2>/dev/null) || return 0
  if printf '%s\n' "${record}" >"${tmp}" 2>/dev/null \
    && mv -f "${tmp}" "${dir}/${account}.json" 2>/dev/null; then
    return 0
  fi
  rm -f "${tmp}"
}
record_usage

cwd=$(field '.workspace.current_dir // .cwd')
line="$(basename "${cwd}")"

model=$(field '.model.display_name')
[[ -n "${model}" ]] && line="${line} · ${model}"

context=$(field '.context_window.used_percentage')
[[ -n "${context}" ]] && line="${line} · ctx $(printf '%.0f' "${context}")%"

five_hour=$(field '.rate_limits.five_hour.used_percentage')
[[ -n "${five_hour}" ]] && line="${line} · 5h $(printf '%.0f' "${five_hour}")%"

[[ -n "${ATC_ACCOUNT:-}" ]] && line="${line} · ${ATC_ACCOUNT}"

printf '%s\n' "${line}"
