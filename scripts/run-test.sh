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

# Тест берётся из RP6_TEST_BIN (scripts/env.sh), а не из жёсткого ./: относительный
# путь работал только при запуске из build/, то есть скрипт нельзя было вызвать из
# репозитория — ровно то, как его и зовут.
TEST="${RP6_TEST_BIN:-/var/home/armada/opencode/build/rp6-vkd3d-sparse-test}"
[ -x "$TEST" ] || { echo "нет теста $TEST — собрать: scripts/build-test.sh" >&2; exit 1; }

# Нужен render-узел; группы video/render у пользователя есть.
exec "$TEST" >> "$OUT" 2>&1
