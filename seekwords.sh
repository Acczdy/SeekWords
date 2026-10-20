#!/usr/bin/env bash
# Linux / macOS GUI launcher. Optional positional arguments preselect directories.
set -eu
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
if ! command -v python3 >/dev/null 2>&1; then
    echo '需要 Python 3.10+（带 Tk）。macOS 可使用 python.org 安装包；Linux 请安装 python3 和 python3-tk。' >&2
    exit 1
fi
if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    echo '需要 Python 3.10 或更新版本。' >&2
    exit 1
fi
exec python3 "$SCRIPT_DIR/seekwords.py" "$@"
