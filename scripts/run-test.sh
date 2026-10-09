#!/bin/bash
# Run of rp6-vkd3d-sparse-test.
#   $1 — path to the ICD manifest (uncommitted) or an empty string for the stock driver
#   $2 — where to write the output (stdout + stderr)
set -uo pipefail

ICD="${1:-}"
OUT="${2:-/var/home/armada/opencode/build/test-run.log}"

if [ -n "$ICD" ]; then
    export VK_DRIVER_FILES="$ICD"
    echo "=== driver: $ICD ===" | tee "$OUT"
else
    unset VK_DRIVER_FILES
    echo "=== driver: stock /usr/lib64/libvulkan_freedreno.so ===" | tee "$OUT"
fi

# The test is taken from RP6_TEST_BIN (scripts/env.sh), not from a hardcoded ./: the
# relative path only worked when run from build/, i.e. the script could not be invoked
# from the repository — exactly how it is called.
TEST="${RP6_TEST_BIN:-/var/home/armada/opencode/build/rp6-vkd3d-sparse-test}"
[ -x "$TEST" ] || { echo "no test at $TEST — build it: scripts/build-test.sh" >&2; exit 1; }

# A render node is needed; the user is in the video/render groups.
exec "$TEST" >> "$OUT" 2>&1
