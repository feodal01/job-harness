#!/bin/sh
# All bootstrap output goes to stderr; stdout belongs exclusively to MCP.
set -eu
umask 077
plugin_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
case "$(uname -s)" in
  Darwin) default_data="$HOME/Library/Application Support/Job Search"; default_cache="$HOME/Library/Caches/job-search" ;;
  Linux) default_data="${XDG_DATA_HOME:-$HOME/.local/share}/job-search"; default_cache="${XDG_CACHE_HOME:-$HOME/.cache}/job-search" ;;
  *) echo "Job Search supports macOS and Linux." >&2; exit 1 ;;
esac
export JOB_SEARCH_DATA_DIR="${JOB_SEARCH_DATA_DIR:-$default_data}"
runtime_cache="${JOB_SEARCH_RUNTIME_DIR:-$default_cache}"
mkdir -p "$runtime_cache"

# Install a private pinned uv if needed; never change shell profiles or the user's Python.
if command -v uv >/dev/null 2>&1; then
  uv_bin=$(command -v uv)
else
  uv_bin="$runtime_cache/uv-0.9.18/uv"
  if [ ! -x "$uv_bin" ]; then
    installer=$(mktemp "$runtime_cache/uv-installer.XXXXXX")
    trap 'rm -f "$installer"' EXIT HUP INT TERM
    curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
      https://astral.sh/uv/0.9.18/install.sh --output "$installer"
    UV_UNMANAGED_INSTALL="$runtime_cache/uv-0.9.18" sh "$installer" >&2
    rm -f "$installer"
    trap - EXIT HUP INT TERM
  fi
fi

# A changed wheel/lock gets its own environment; existing services keep their runtime.
if command -v shasum >/dev/null 2>&1; then
  runtime_hash=$(shasum -a 256 "$plugin_root/runtime/uv.lock" | cut -d ' ' -f 1)
else
  runtime_hash=$(sha256sum "$plugin_root/runtime/uv.lock" | cut -d ' ' -f 1)
fi
export UV_PROJECT_ENVIRONMENT="$runtime_cache/environments/$runtime_hash"
export UV_PYTHON_INSTALL_DIR="$runtime_cache/python"
# Leave the source checkout completely: only the wheel inside this plugin is imported.
cd "$plugin_root/runtime"
exec "$uv_bin" run --project "$plugin_root/runtime" --locked --no-dev --python 3.12 \
  python -I -m src.mcp
