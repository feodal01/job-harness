#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "Usage: init-artifacts.sh <working-directory>" >&2
  exit 2
fi

WORKDIR=$1
ROOT="${WORKDIR%/}/.job-harness"

mkdir -p "$ROOT/briefs"
mkdir -p "$ROOT/companies"

if [ ! -f "$ROOT/companies/careers.json" ]; then
  printf '{}\n' > "$ROOT/companies/careers.json"
fi

printf '%s\n' "$ROOT"
