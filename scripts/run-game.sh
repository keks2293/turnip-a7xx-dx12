#!/usr/bin/env bash
# Launch of RE4 (DX12) via proton-cachyos on stock or patched turnip.
#
# The game is not registered in Steam, the prefix was created by Heroic (sideload), so
# the launch goes directly via proton from /usr/share/steam/compatibilitytools.d.
#
#   scripts/run-game.sh patched [sec]   # patched driver from build/out
#   scripts/run-game.sh stock           # packaged driver from /usr/lib64
#
# TAG=trace sets the log suffix (game-patched-trace.log): without it every
# run overwrites the previous one, while logs of different runs are needed at once.
#
# GAMEARGS="..." — extra arguments to the game. Needed to change the API:
# the RE4 engine picks it with the via.render.RenderDeviceAPI key, values —
# DirectX11 / DirectX12 / OpenGL / Vulkan (strings in re4.exe). The API after
# launch is read from [Render] Capability in local_config.ini, where the game
# writes the actually selected one — you cannot tell by eye.
#
# The driver is slipped to the loader via VK_DRIVER_FILES: /usr must not be touched,
# sudo requires a password (docs/analysis.md, section 7).
set -euo pipefail

MODE="${1:-patched}"
TIMEOUT="${2:-180}"

GAME_DIR="/run/media/armada/Z Slim/Games/Resident.Evil.4.Gold.Edition-InsaneRamZes"
GAME_EXE="$GAME_DIR/re4.exe"
PREFIX="/var/home/armada/Games/Heroic/Prefixes/re4"
PROTON="/usr/share/steam/compatibilitytools.d/proton-cachyos-11.0-arm64/proton"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATCHED_ICD="$REPO/../build/out/freedreno_icd.json"

case "$MODE" in
  patched)
    [ -r "$PATCHED_ICD" ] || { echo "no $PATCHED_ICD — build it: scripts/build-turnip.sh" >&2; exit 1; }
    export VK_DRIVER_FILES="$PATCHED_ICD"
    ;;
  stock)
    unset VK_DRIVER_FILES || true
    ;;
  *) echo "usage: $0 [stock|patched] [timeout-sec]" >&2; exit 1 ;;
esac

# DRIRC_CONFIGDIR is no longer set anywhere: the driver is built with --prefix=/usr and
# reads /usr/share/drirc.d itself (docs/analysis.md 10.4). unset, rather than "do
# nothing", — so that a stray variable in the caller's environment cannot swap
# driconf and make the run look successful for the wrong reason.
#
# DRIRC_TEST — the only exception, and only for experiments: a directory with
# a modified driconf. Needed to measure the cost of individual turnip options —
# in particular tu_enable_softfloat32, which is not in any env override of the
# driver and so cannot be disabled otherwise (docs/analysis.md 11.1b). The name is
# separate from DRIRC_CONFIGDIR so that the "experimental" path cannot be set by a
# normal variable without noticing it.
unset DRIRC_CONFIGDIR
if [ -n "${DRIRC_TEST:-}" ]; then
    [ -d "$DRIRC_TEST" ] || { echo "no directory $DRIRC_TEST" >&2; exit 1; }
    export DRIRC_CONFIGDIR="$DRIRC_TEST"
fi

[ -x "$PROTON" ] || { echo "no proton: $PROTON" >&2; exit 1; }
[ -f "$GAME_EXE" ] || { echo "no game: $GAME_EXE" >&2; exit 1; }
[ -d "$PREFIX" ]  || { echo "no prefix: $PREFIX" >&2; exit 1; }

# The full run log (raw) — into results/raw, it is in .gitignore: there are thousands of
# lines of wine/FEX noise that say nothing about the cause. What goes into git is the
# filtered results/game-<mode>.log, assembled by scripts/curate-game-log.sh.
mkdir -p "$REPO/results/raw"
SUFFIX="${TAG:+-$TAG}"
RAW="$REPO/results/raw/game-$MODE$SUFFIX.log"
LOG="$REPO/results/game-$MODE$SUFFIX.log"
CURATE="$REPO/scripts/curate-game-log.sh"

export STEAM_COMPAT_DATA_PATH="$PREFIX"
# proton requires a path to the steam client (proton:991) — otherwise KeyError at setup_prefix.
export STEAM_COMPAT_CLIENT_INSTALL_PATH="/var/home/armada/.local/share/Steam"
# The AppID is needed by protonfixes: without it the RE4 fixes are not applied ("UNKNOWN (4)").
# 2050650 — Resident Evil 4, needed only to pick the fixes, prefix_id is unrelated.
export STEAM_COMPAT_APP_ID=2050650
export PROTON_LOG=1          # /tmp/proton-$USER/*.log
export WINEDEBUG=-all
# DXVK for DX12 in proton is the standard path (dxgi DXVK + d3d12 vkd3d-proton), it
# does not need to be disabled. DLL edits in the prefix are useless: proton restores
# them from default_pfx on every launch. We catch the vkCreateInstance rejection at the
# loader level; for the full picture on ICD: VK_LOADER_DEBUG=all scripts/run-game.sh
export VK_LOADER_DEBUG="${VK_LOADER_DEBUG:-error,warn}"

# MangoHud — a Vulkan layer, not DXVK, so it works on the vkd3d DX12 path too.
# DXVK_HUD is useless here: RE4 goes through d3d12 = vkd3d-proton, DXVK does not take
# part in this path. The layer is enabled implicitly (implicit_layer.d), activated by
# the MANGOHUD=1 variable — off by default, as it should be.
#
#   HUD=1    overlay on screen
#   HUDLOG=N overlay + CSV in $HUD_DIR: N seconds of log.
#            Needed because FPS cannot be counted off the screen, and for comparing
#            runs numbers are needed, not a picture.
#
# HUDINTERVAL — sample period in ms, default 1000 (once per second).
# Historically it was hard-coded to 1000, and that is not enough: over a 150 s run
# n≈85 samples, and the p50 spread between runs that are known to be equivalent
# reaches 24–43 ms (docs/analysis.md, section 15 and results/fps-patched-*.csv).
# On such noise an effect below ~40% cannot be separated in principle, so
# for frametime comparisons set 100 (n≈1500). For earlier series
# behavior does not change — the default value is the same.
#
# MangoHud logs go to /tmp (tmpfs) on purpose: the game folder is exFAT, and a CSV of
# several hundred lines per run — on exFAT that is extra writes.
HUD_DIR=/tmp/opencode/mangologs

if [ -n "${HUDLOG:-}" ]; then
    rm -rf "$HUD_DIR"
    mkdir -p "$HUD_DIR"
    export MANGOHUD=1
    export MANGOHUD_CONFIG="autostart_log,log_interval=${HUDINTERVAL:-1000},log_duration=${HUDLOG},output_folder=${HUD_DIR}"
elif [ -n "${HUD:-}" ]; then
    export MANGOHUD=1
fi

echo "=== $MODE ==="
echo "game:   $GAME_EXE"
echo "prefix: $PREFIX"
echo "driver: ${VK_DRIVER_FILES:-stock /usr/lib64}"
echo "log:    $LOG  (full: $RAW)"
echo

# Which .so exactly gets loaded — recorded in the log, otherwise a run
# cannot be told apart from a silent rollback to the packaged driver.
#
# On stock there are TWO devices visible (Adreno + llvmpipe), so there are two
# sparseResidencyImage2D lines too — they must be labeled, otherwise it is unclear
# which one matters. With VK_DRIVER_FILES there is one device.
{
  echo "# date: $(date -Is)"
  echo "# VK_DRIVER_FILES=${VK_DRIVER_FILES:-<unset>}"
  echo "# DRIRC_CONFIGDIR=${DRIRC_CONFIGDIR:-<unset>}"
  echo "# MANGOHUD=${MANGOHUD:-<unset>}"
  echo "# MANGOHUD_CONFIG=${MANGOHUD_CONFIG:-<unset>}"
  echo "# devices (name | sparseResidencyImage2D):"
  vulkaninfo 2>/dev/null | awk '
    /deviceName/ { dev=$3" "$4" "$5" "$6" "$7 }
    /sparseResidencyImage2D/ { print "#   " dev " | " $3; dev="" }
  '
  echo "# ---"
} > "$RAW"

rm -f /tmp/proton-"${USER:-armada}"/*.log 2>/dev/null || true

# CWD must be the game folder: RE4 looks for re_chunk_000.pak and local_config.ini
# relative to the current directory, otherwise it writes an empty config in the wrong place.
cd "$GAME_DIR"

set +e
# ${EXTRA[@]+...} — array expansion compatible with set -u when empty.
read -r -a EXTRA <<< "${GAMEARGS:-}"
timeout --foreground --signal=INT "$TIMEOUT" \
  "$PROTON" waitforexitandrun "$GAME_EXE" ${EXTRA[@]+"${EXTRA[@]}"} >>"$RAW" 2>&1
RC=$?
set -e

# timeout kills proton but not the game: re4.exe and wineserver survive SIGINT and
# stay alive. The next run then starts on top of them, into the same wineserver,
# and two sets of d3d12_device_create show up in the log — run numbers get mixed.
# So after a timeout we clean up the processes and say so explicitly.
LEFTOVER="$(pgrep -f 're4\.exe' 2>/dev/null || true)"
if [ -n "$LEFTOVER" ]; then
    echo "the game survived the timeout (pid $LEFTOVER) — killing it, otherwise the next run"
    echo "will start on top of it and the logs will mix"
    pkill -f 're4\.exe' 2>/dev/null || true
    sleep 2
    pkill -f 'wineserver' 2>/dev/null || true
    sleep 1
    pgrep -f 're4\.exe' >/dev/null 2>&1 && pkill -9 -f 're4\.exe' 2>/dev/null || true
fi

echo
echo "exit code: $RC"
echo -n "API from local_config.ini: "
grep -m1 '^Capability=' "$GAME_DIR/local_config.ini" 2>/dev/null || echo "(no key)"

# Full log -> filtered (results/game-<mode>.log).
"$CURATE" "$RAW" "$LOG"

# Copy of the proton log (PROTON_LOG=1) — into raw: for some launches it is not created,
# so only existing ones are copied.
for f in /tmp/proton-"${USER:-armada}"/*.log; do
  [ -e "$f" ] && cp "$f" "$REPO/results/raw/proton-$MODE$SUFFIX-$(basename "$f")"
done

exit $RC
