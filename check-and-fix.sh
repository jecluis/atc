#!/usr/bin/env bash
#
# Format, lint, type check, and test atc.
#
#   ./check-and-fix.sh          fix what can be fixed, then verify
#   ./check-and-fix.sh --check   verify only, changing nothing

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

check_only=false
case "${1:-}" in
    --check) check_only=true ;;
    "") ;;
    *)
        echo "usage: ${0##*/} [--check]" >&2
        exit 2
        ;;
esac

run() {
    echo "==> $*"
    "$@"
}

if [[ "${check_only}" == true ]]; then
    run uv run ruff format --check src tests
    run uv run ruff check src tests
else
    run uv run ruff format src tests
    run uv run ruff check --fix src tests
fi

run uv run basedpyright
run uv run pytest -q
