#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

if [[ -z "${TYPESAFE_API_KEY:-}" ]]; then
  echo "TYPESAFE_API_KEY is required." >&2
  exit 1
fi

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "OPENAI_API_KEY is required." >&2
  exit 1
fi

cd "${REPO_ROOT}"
uv sync --frozen

if [[ ! -f build/extension/dc/dc.duckdb_extension ]]; then
  ./build.sh
fi

exec uv run python examples/chain-command/server.py
