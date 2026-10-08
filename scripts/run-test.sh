#!/bin/bash
# Прогон rp6-vkd3d-sparse-test.
#   $1 — путь к манифесту ICD (нескаргed) или пустая строка для стокового драйвера
#   $2 — куда писать вывод (stdout + stderr)
set -uo pipefail

ICD="${1:-}"
OUT="${2:-/var/home/armada/opencode/build/test-run.log}"

if [ -n "$ICD" ]; then
    export VK_DRIVER_FILES="$ICD"
    echo "=== драйвер: $ICD ===" | tee "$OUT"
else
    unset VK_DRIVER_FILES
    echo "=== драйвер: стоковый /usr/lib64/libvulkan_freedreno.so ===" | tee "$OUT"
fi

# Нужен render-узел; группы video/render у пользователя есть.
exec ./rp6-vkd3d-sparse-test >> "$OUT" 2>&1
