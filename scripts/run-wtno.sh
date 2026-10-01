#!/usr/bin/env bash
# Запуск Wolfenstein: The New Order (GOG, id Tech 5) через proton-cachyos —
# диагностика просадок FPS.
#
#   scripts/run-wtno.sh [timeout-sec]     # по умолчанию 180
#
# Игра добавлена в Heroic (sideload Vw7qEkk2ogxTfZJfyx4ejA), но прогон идёт
# напрямую через proton, как run-game.sh для RE4: так получаем CSV MangoHud и
# отфильтрованный лог без действий в GUI.
#
# Рендер — OpenGL (id Tech 5): путь wine opengl32 → стоковый GL-драйвер
# (kgsl_dri), turnip НЕ участвует, VK_DRIVER_FILES не подсовывается.
# WINE_FULLSCREEN_FSR повторяет включённый в Heroic FSR-хак протона
# (GamesConfig/Vw7qEkk2ogxTfZJfyx4ejA.json: enableFSR, maxSharpness=5).
set -euo pipefail

TIMEOUT="${1:-180}"
HUDLOG="${HUDLOG:-$TIMEOUT}"

GAME_DIR="/run/media/armada/Z Slim/Games/Wolfenstein The New Order"
GAME_EXE="$GAME_DIR/WolfNewOrder_x64.exe"
# Имя префикса обязано содержать цифру: protonfixes вытаскивает номер игры из
# STEAM_COMPAT_DATA_PATH (re.findall(r'\d+')) и падает без цифр (wtno → IndexError).
PREFIX="/var/home/armada/Games/Heroic/Prefixes/wtno2014"
PROTON="/usr/share/steam/compatibilitytools.d/proton-cachyos-11.0-arm64/proton"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$REPO/results/raw"
SUFFIX="${TAG:+-$TAG}"
RAW="$REPO/results/raw/game-wtno$SUFFIX.log"
LOG="$REPO/results/game-wtno$SUFFIX.log"
CURATE="$REPO/scripts/curate-game-log.sh"

[ -x "$PROTON" ] || { echo "нет proton: $PROTON" >&2; exit 1; }
[ -f "$GAME_EXE" ] || { echo "нет игры: $GAME_EXE" >&2; exit 1; }
# proton кладёт pfx.lock в корень compatdata сам, но только если каталог уже
# существует (Steam/Heroic его создают заранее) — иначе FileNotFoundError.
mkdir -p "$PREFIX"

export STEAM_COMPAT_DATA_PATH="$PREFIX"
# proton требует путь к steam-клиенту — иначе KeyError на setup_prefix.
export STEAM_COMPAT_CLIENT_INSTALL_PATH="/var/home/armada/.local/share/Steam"
# AppID (214870 — TNO в Steam) нужен только protonfixes: по нему выбираются
# фиксы, с GOG-сборкой они безвредны. prefix_id с AppID не связан.
export STEAM_COMPAT_APP_ID=214870
export PROTON_LOG=1          # /tmp/proton-$USER/*.log
export WINEDEBUG=-all
export VK_LOADER_DEBUG="${VK_LOADER_DEBUG:-error,warn}"

# FSR-хак протона — как в записи Heroic; работает только в полноэкранном
# режиме. MangoHud: оверлей + CSV раз в секунду, лог в tmpfs (игровая папка
# на exFAT, CSV писать туда лишнее).
export WINE_FULLSCREEN_FSR=1
export WINE_FULLSCREEN_FSR_STRENGTH=5
export MANGOHUD=1
export MANGOHUD_CONFIG="autostart_log,log_interval=1000,log_duration=${HUDLOG},output_folder=/tmp/opencode/mangologs"
mkdir -p /tmp/opencode/mangologs

{
  echo "# date: $(date -Is)"
  echo "# exe: $GAME_EXE"
  echo "# render: OpenGL (id Tech 5) — стоковый GL kgsl_dri, turnip не участвует"
  echo "# WINE_FULLSCREEN_FSR=$WINE_FULLSCREEN_FSR strength=$WINE_FULLSCREEN_FSR_STRENGTH"
  echo "# MANGOHUD=${MANGOHUD:-<unset>}"
  echo "# MANGOHUD_CONFIG=$MANGOHUD_CONFIG"
  echo "# ---"
} > "$RAW"

rm -f /tmp/proton-"${USER:-armada}"/*.log 2>/dev/null || true

# CWD — папка игры: id Tech 5 ищет base/ и конфиги относительно неё.
cd "$GAME_DIR"

set +e
# Обёртка mangohud обязательна: MANGOHUD=1 ставит только Vulkan-слой, а у
# OpenGL-игры хук (LD_PRELOAD libMangoHud) кладётся только обёрткой — иначе
# CSV не появляется. Внутри обёртки MANGOHUD_CONFIG действует как обычно.
timeout --foreground --signal=INT "$TIMEOUT" \
  mangohud "$PROTON" waitforexitandrun "$GAME_EXE" >>"$RAW" 2>&1
RC=$?
set -e

# timeout убивает proton, но не игру: wineserver переживает SIGINT. Остатки
# убиваем по pid из pgrep, а не pkill -f — маска захватывает постороннее.
LEFTOVER="$(pgrep -f 'WolfNewOrder_x64\.exe' 2>/dev/null || true)"
if [ -n "$LEFTOVER" ]; then
    echo "игра пережила таймаут (pid $LEFTOVER) — убиваю, иначе следующий прогон"
    echo "пойдёт поверх неё и логи смешаются"
    kill $LEFTOVER 2>/dev/null || true
    sleep 2
    kill $(pgrep -f 'WolfNewOrder_x64\.exe') 2>/dev/null || true
    sleep 1
    kill -9 $(pgrep -f 'WolfNewOrder_x64\.exe') 2>/dev/null || true
fi

echo
echo "код возврата: $RC"

# Полный лог -> отфильтрованный (results/game-wtno*.log).
"$CURATE" "$RAW" "$LOG"

# Копия лога proton (PROTON_LOG=1) — в raw: у части запусков он не создаётся.
for f in /tmp/proton-"${USER:-armada}"/*.log; do
  [ -e "$f" ] && cp "$f" "$REPO/results/raw/proton-wtno$SUFFIX-$(basename "$f")"
done

echo "CSV MangoHud: $(ls -t /tmp/opencode/mangologs/*.csv 2>/dev/null | head -1)"
exit $RC
