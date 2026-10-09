#!/bin/zsh
set -e
cd "$(dirname "$0")"
if ! command -v uv >/dev/null; then
  print "Для запуска нужен uv: https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi
exec uv run python -m src
