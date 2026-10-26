#!/usr/bin/env bash
#
# Single source of truth for markdown formatting: which prettier and
# markdownlint-cli2 versions are pinned and how they're invoked. Run by
# check-and-fix.sh and after edits to a markdown file — nothing else should
# invoke either tool directly for markdown.
#
# There is no package.json/lockfile in this repo, so tool versions are
# pinned here instead.

set -euo pipefail

readonly PRETTIER_VERSION="3.9.6"
readonly MARKDOWNLINT_CLI2_VERSION="0.23.2"

# Physical paths, as realpath gives the files (see below), so a repo reached
# through a symbolic link still contains them.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd -P)"
readonly SCRIPT_DIR
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." &>/dev/null && pwd -P)"
readonly REPO_ROOT
readonly MDLINT_CONFIG="${REPO_ROOT}/.markdownlint-cli2.jsonc"

usage() {
  cat <<'EOF'
Usage: normalize-markdown.sh [--check|--fix] [--prettier|--markdownlint]
                             [file.md ...]

Runs prettier and/or markdownlint-cli2 against markdown files, using this
repo's pinned tool versions and config.

Mode (mutually exclusive, default --check):
  --check         Verify formatting only; exits non-zero if anything is not
                  formatted / fails lint. Runs:
                    prettier --no-color --check <file...>
  --fix           Rewrite files in place. Runs:
                    prettier --no-color --check --write <file...>
                  markdownlint-cli2 stays check-only even in --fix mode
                  UNLESS run alone via --markdownlint (see below) — running
                  it alongside prettier's rewrite risks the two tools'
                  fixes conflicting with each other.

Tool selection (default: both):
  --prettier      Run prettier only.
  --markdownlint  Run markdownlint-cli2 only. Combined with --fix, this
                  passes --fix to markdownlint-cli2 itself (safe in
                  isolation, since prettier isn't also rewriting the same
                  files in this invocation).

  -h, --help      Show this help and exit.

Files:
  Any arguments not matching an option above are treated as files, and must
  end in ".md" and be in this repo. With no file arguments, every markdown
  file in the repo is targeted via prettier/markdownlint-cli2's own "**/*.md"
  glob — passed as a literal string argument, not shell-expanded, so their
  own globbing + ignore-file handling applies.

Examples:
  normalize-markdown.sh                         # check everything
  normalize-markdown.sh --fix                   # fix everything
  normalize-markdown.sh --fix README.md         # fix one file
  normalize-markdown.sh --prettier --fix README.md
  normalize-markdown.sh --markdownlint --fix README.md
EOF
}

mode=""
tool_prettier=""
tool_markdownlint=""
files=()

for arg in "$@"; do
  case "${arg}" in
    -h | --help)
      usage
      exit 0
      ;;
    --check)
      if [[ "${mode}" == "fix" ]]; then
        echo "error: --check and --fix are mutually exclusive" >&2
        exit 2
      fi
      mode="check"
      ;;
    --fix)
      if [[ "${mode}" == "check" ]]; then
        echo "error: --check and --fix are mutually exclusive" >&2
        exit 2
      fi
      mode="fix"
      ;;
    --prettier)
      tool_prettier=1
      ;;
    --markdownlint)
      tool_markdownlint=1
      ;;
    -*)
      echo "error: unknown option '${arg}'" >&2
      exit 2
      ;;
    *)
      files+=("${arg}")
      ;;
  esac
done

readonly mode="${mode:-check}"

# No tool flag given at all: run both (the default). Otherwise, run exactly
# whichever tool(s) were explicitly requested.
if [[ -z "${tool_prettier}" && -z "${tool_markdownlint}" ]]; then
  tool_prettier=1
  tool_markdownlint=1
fi
readonly tool_prettier
readonly tool_markdownlint

for i in "${!files[@]}"; do
  f="${files[i]}"
  if [[ "${f}" != *.md ]]; then
    echo "error: '${f}' is not a markdown (.md) file" >&2
    exit 2
  fi
  # The tools run from the repo root (see below), and markdownlint-cli2
  # matches its ignores against paths relative to it.
  f="$(realpath -m -- "${f}")"
  if [[ "${f}" != "${REPO_ROOT}"/* ]]; then
    echo "error: '${files[i]}' is not in this repo" >&2
    exit 2
  fi
  files[i]="${f#"${REPO_ROOT}"/}"
done

if [[ "${#files[@]}" -eq 0 ]]; then
  files=("**/*.md")
fi

cd "${REPO_ROOT}"

# Prefer `pnx` (pnpm's dlx-equivalent, https://pnpm.io/cli/pnx) over `npx`,
# but only when pnpm's store is writable: a sandboxed session may read it
# without writing it, and pnx then fails before running anything.
# --prefer-offline lets a cached copy of the pinned version run without the
# registry.
if command -v pnx &>/dev/null \
  && [[ -w "$(pnpm store path 2>/dev/null)" ]]; then
  readonly runner=(pnx)
else
  readonly runner=(npx --yes --prefer-offline)
fi

run_pkg() {
  "${runner[@]}" "$@"
}

status=0

if [[ -n "${tool_prettier}" ]]; then
  prettier_args=(--no-color --check)
  [[ "${mode}" == "fix" ]] && prettier_args+=(--write)

  if ! run_pkg "prettier@${PRETTIER_VERSION}" \
    "${prettier_args[@]}" "${files[@]}"; then
    status=1
  fi
fi

if [[ -n "${tool_markdownlint}" ]]; then
  mdlint_args=(--config "${MDLINT_CONFIG}")
  # Only let markdownlint-cli2 write when it's the sole tool running this
  # invocation — alongside prettier, it stays check-only (see usage()).
  if [[ "${mode}" == "fix" && -z "${tool_prettier}" ]]; then
    mdlint_args+=(--fix)
  fi

  if ! run_pkg "markdownlint-cli2@${MARKDOWNLINT_CLI2_VERSION}" \
    "${mdlint_args[@]}" "${files[@]}"; then
    status=1
  fi
fi

exit "${status}"
