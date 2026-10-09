#!/usr/bin/env bash
# Launch of Wolfenstein: The New Order (GOG, id Tech 5) via proton-cachyos —
# diagnostics of FPS drops.
#
#   scripts/run-wtno.sh [timeout-sec]     # 180 by default
#
# The game was added to Heroic (sideload Vw7qEkk2ogxTfZJfyx4ejA), but the run goes
# directly via proton, like run-game.sh for RE4: this way we get the MangoHud CSV and
# a filtered log without GUI actions.
#
# Rendering is OpenGL (id Tech 5): the path wine opengl32 → stock GL driver
# (kgsl_dri), turnip does NOT take part, VK_DRIVER_FILES is not slipped in.
# WINE_FULLSCREEN_FSR repeats the FSR hack of proton enabled in Heroic
# (GamesConfig/Vw7qEkk2ogxTfZJfyx4ejA.json: enableFSR, maxSharpness=5).
set -euo pipefail

TIMEOUT="${1:-180}"
HUDLOG="${HUDLOG:-$TIMEOUT}"

GAME_DIR="/run/media/armada/Z Slim/Games/Wolfenstein The New Order"
GAME_EXE="$GAME_DIR/WolfNewOrder_x64.exe"
# The prefix name must contain a digit: protonfixes pulls the game number out of
# STEAM_COMPAT_DATA_PATH (re.findall(r'\d+')) and fails without digits (wtno → IndexError).
PREFIX="/var/home/armada/Games/Heroic/Prefixes/wtno2014"
PROTON="/usr/share/steam/compatibilitytools.d/proton-cachyos-11.0-arm64/proton"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$REPO/results/raw"
SUFFIX="${TAG:+-$TAG}"
RAW="$REPO/results/raw/game-wtno$SUFFIX.log"
LOG="$REPO/results/game-wtno$SUFFIX.log"
CURATE="$REPO/scripts/curate-game-log.sh"

[ -x "$PROTON" ] || { echo "no proton: $PROTON" >&2; exit 1; }
[ -f "$GAME_EXE" ] || { echo "no game: $GAME_EXE" >&2; exit 1; }
# proton puts pfx.lock into the compatdata root itself, but only if the directory
# already exists (Steam/Heroic create it in advance) — otherwise FileNotFoundError.
mkdir -p "$PREFIX"

export STEAM_COMPAT_DATA_PATH="$PREFIX"
# proton requires a path to the steam client — otherwise KeyError at setup_prefix.
export STEAM_COMPAT_CLIENT_INSTALL_PATH="/var/home/armada/.local/share/Steam"
# The AppID (214870 — TNO on Steam) is needed only by protonfixes: fixes are picked
# by it, with the GOG build they are harmless. prefix_id is unrelated to AppID.
export STEAM_COMPAT_APP_ID=214870
export PROTON_LOG=1          # /tmp/proton-$USER/*.log
export WINEDEBUG=-all
export VK_LOADER_DEBUG="${VK_LOADER_DEBUG:-error,warn}"

# proton's FSR hack — as in the Heroic entry; works only in fullscreen
# mode. MangoHud: overlay + CSV once per second, log in tmpfs (the game folder is
# on exFAT, writing the CSV there is extra writes).
export WINE_FULLSCREEN_FSR=1
export WINE_FULLSCREEN_FSR_STRENGTH=5
export MANGOHUD=1
export MANGOHUD_CONFIG="autostart_log,log_interval=1000,log_duration=${HUDLOG},output_folder=/tmp/opencode/mangologs"
mkdir -p /tmp/opencode/mangologs

{
  echo "# date: $(date -Is)"
  echo "# exe: $GAME_EXE"
  echo "# render: OpenGL (id Tech 5) — stock GL kgsl_dri, turnip does not take part"
  echo "# WINE_FULLSCREEN_FSR=$WINE_FULLSCREEN_FSR strength=$WINE_FULLSCREEN_FSR_STRENGTH"
  echo "# MANGOHUD=${MANGOHUD:-<unset>}"
  echo "# MANGOHUD_CONFIG=$MANGOHUD_CONFIG"
  echo "# ---"
} > "$RAW"

rm -f /tmp/proton-"${USER:-armada}"/*.log 2>/dev/null || true

# CWD — the game folder: id Tech 5 looks for base/ and configs relative to it.
cd "$GAME_DIR"

set +e
# The mangohud wrapper is mandatory: MANGOHUD=1 only sets the Vulkan layer, while for
# an OpenGL game the hook (LD_PRELOAD libMangoHud) is laid down only by the wrapper —
# otherwise the CSV does not appear. Inside the wrapper MANGOHUD_CONFIG works as usual.
timeout --foreground --signal=INT "$TIMEOUT" \
  mangohud "$PROTON" waitforexitandrun "$GAME_EXE" >>"$RAW" 2>&1
RC=$?
set -e

# timeout kills proton but not the game: wineserver survives SIGINT. The leftovers
# are killed by pid from pgrep, not pkill -f — the pattern would catch unrelated things.
LEFTOVER="$(pgrep -f 'WolfNewOrder_x64\.exe' 2>/dev/null || true)"
if [ -n "$LEFTOVER" ]; then
    echo "the game survived the timeout (pid $LEFTOVER) — killing it, otherwise the next run"
    echo "will start on top of it and the logs will mix"
    kill $LEFTOVER 2>/dev/null || true
    sleep 2
    kill $(pgrep -f 'WolfNewOrder_x64\.exe') 2>/dev/null || true
    sleep 1
    kill -9 $(pgrep -f 'WolfNewOrder_x64\.exe') 2>/dev/null || true
fi

echo
echo "exit code: $RC"

# Full log -> filtered (results/game-wtno*.log).
"$CURATE" "$RAW" "$LOG"

# Copy of the proton log (PROTON_LOG=1) — into raw: for some launches it is not created.
for f in /tmp/proton-"${USER:-armada}"/*.log; do
  [ -e "$f" ] && cp "$f" "$REPO/results/raw/proton-wtno$SUFFIX-$(basename "$f")"
done

echo "CSV MangoHud: $(ls -t /tmp/opencode/mangologs/*.csv 2>/dev/null | head -1)"
exit $RC
