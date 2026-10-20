#!/bin/bash
# macOS Finder double-click launcher.
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
bash "$SCRIPT_DIR/seekwords.sh" "$@"
result=$?
if [ "$result" -ne 0 ]; then
    printf '\n启动失败。按回车关闭窗口。\n'
    read -r _answer
fi
exit "$result"
