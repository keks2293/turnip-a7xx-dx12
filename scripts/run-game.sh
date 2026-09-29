#!/usr/bin/env bash
# Запуск RE4 (DX12) через proton-cachyos на стоковом или патченом turnip.
#
# Игра не зарегистрирована в Steam, префикс создат��н Heroic (sideload), поэтому
# запуск идёт напрямую через proton из /usr/share/steam/compatibilitytools.d.
#
#   scripts/run-game.sh patched [sec]   # патченый драйвер из build/out
#   scripts/run-game.sh stock           # пакетный драйвер из /usr/lib64
#
# TAG=trace задаёт суффикс логов (game-patched-trace.log): без него каждый
# прогон затирает предыдущий, а логи разных прогонов нужны одновременно.
#
# GAMEARGS="..." — дополнительные аргументы к игре. Нужны для смены API:
# движок RE4 выбирает его ключом via.render.RenderDeviceAPI, значения —
# DirectX11 / DirectX12 / OpenGL / Vulkan (строки в re4.exe). API после
# запуска читается из [Render] Capability в local_config.ini, куда игра
# записывает фактически выбранный — на глаз это не определить.
#
# Dрайвер подсовывается загрузчику через VK_DRIVER_FILES: /usr трогать нельзя,
# sudo требует пароль (docs/analysis.md, раздел 7).
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
    [ -r "$PATCHED_ICD" ] || { echo "нет $PATCHED_ICD — собрать: scripts/build-turnip.sh" >&2; exit 1; }
    export VK_DRIVER_FILES="$PATCHED_ICD"
    ;;
  stock)
    unset VK_DRIVER_FILES || true
    ;;
  *) echo "usage: $0 [stock|patched] [timeout-sec]" >&2; exit 1 ;;
esac

# DRIRC_CONFIGDIR больше не задаётся нигде: драйвер собран с --prefix=/usr и
# читает /usr/share/drirc.d сам (docs/analysis.md 10.4). unset, а не «ничего не
# делать», — чтобы случайная переменная в окружении вызывающего не подменила
# driconf и прогон не выглядел бы удачным по чужой причине.
unset DRIRC_CONFIGDIR

[ -x "$PROTON" ] || { echo "нет proton: $PROTON" >&2; exit 1; }
[ -f "$GAME_EXE" ] || { echo "нет игры: $GAME_EXE" >&2; exit 1; }
[ -d "$PREFIX" ]  || { echo "нет префикса: $PREFIX" >&2; exit 1; }

# Полный лог прогона (raw) — в results/raw, он в .gitignore: там тысячи строк
# шума wine/FEX, которые не говорят ничего о причине. В git идёт отфильтрованный
# results/game-<mode>.log, собранный scripts/curate-game-log.sh.
mkdir -p "$REPO/results/raw"
SUFFIX="${TAG:+-$TAG}"
RAW="$REPO/results/raw/game-$MODE$SUFFIX.log"
LOG="$REPO/results/game-$MODE$SUFFIX.log"
CURATE="$REPO/scripts/curate-game-log.sh"

export STEAM_COMPAT_DATA_PATH="$PREFIX"
# proton требует путь к steam-клиенту (proton:991) — иначе KeyError на setup_prefix.
export STEAM_COMPAT_CLIENT_INSTALL_PATH="/var/home/armada/.local/share/Steam"
# AppID нужен protonfixes: без него RE4-фиксы не применяются ("UNKNOWN (4)").
# 2050650 — Resident Evil 4, нужен только для выбора фиксов, prefix_id не связан.
export STEAM_COMPAT_APP_ID=2050650
export PROTON_LOG=1          # /tmp/proton-$USER/*.log
export WINEDEBUG=-all
# DXVK для DX12 в proton — штатный путь (dxgi DXVK + d3d12 vkd3d-proton), гасить
# его не нужно. Правки DLL в префиксе бесполезны: proton восстанавливает их из
# default_pfx при каждом запуске. Ловим отказ vkCreateInstance на уровне
# загрузчика; для полной картины по ICD: VK_LOADER_DEBUG=all scripts/run-game.sh
export VK_LOADER_DEBUG="${VK_LOADER_DEBUG:-error,warn}"

# MangoHud — слой Vulkan, а не DXVK, поэтому работает и на vkd3d-пути DX12.
# DXVK_HUD здесь бесполезен: RE4 идёт через d3d12 = vkd3d-proton, DXVK в этом
# пути не участвует. Слой включён неявно (implicit_layer.d), активируется
# переменной MANGOHUD=1 — по умолчанию выключен, как и положено.
#
#   HUD=1    оверлей на экране
#   HUDLOG=N оверлей + CSV в $HUD_DIR: N секунд лога, сэмпл раз в секунду.
#            Нужно потому, что с экрана FPS не считать, а для сравнения
#            прогонов нужны числа, а не картинка.
#
# Логи MangoHud пишутся в /tmp (tmpfs) намеренно: папка игры на exFAT, а CSV
# каждый прогон на несколько сотен строк — на exFAT это лишние записи.
HUD_DIR=/tmp/opencode/mangologs

if [ -n "${HUDLOG:-}" ]; then
    rm -rf "$HUD_DIR"
    mkdir -p "$HUD_DIR"
    export MANGOHUD=1
    export MANGOHUD_CONFIG="autostart_log,log_interval=1000,log_duration=${HUDLOG},output_folder=${HUD_DIR}"
elif [ -n "${HUD:-}" ]; then
    export MANGOHUD=1
fi

echo "=== $MODE ==="
echo "игра:   $GAME_EXE"
echo "префикс:$PREFIX"
echo "драйвер:${VK_DRIVER_FILES:-сток /usr/lib64}"
echo "лог:    $LOG  (полный: $RAW)"
echo

# Какой именно .so реально загрузится — фиксируем в лог, иначе прогон
# нельзя отличить от молчаливого отката на пакетный драйвер.
#
# В стоке видно ДВА устройства (Adreno + llvmpipe), поэтому строк с
# sparseResidencyImage2D тоже две — их надо подписывать, иначе непонятно,
# какая относится к делу. С VK_DRIVER_FILES устройство одно.
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

# CWD обязан быть папкой игры: RE4 ищет re_chunk_000.pak и local_config.ini
# относительно текущего каталога, иначе пишет пустой конфиг не туда.
cd "$GAME_DIR"

set +e
# ${EXTRA[@]+...} — разворачивание массива, совместимое с set -u при пустом.
read -r -a EXTRA <<< "${GAMEARGS:-}"
timeout --foreground --signal=INT "$TIMEOUT" \
  "$PROTON" waitforexitandrun "$GAME_EXE" ${EXTRA[@]+"${EXTRA[@]}"} >>"$RAW" 2>&1
RC=$?
set -e

# timeout убивает proton, но не игру: re4.exe и wineserver переживают SIGINT и
# остаются жить. Следующий прогон тогда стартует поверх них, в тот же wineserver,
# и в логе видно два набора d3d12_device_create — числа прогона смешиваются.
# Поэтому после таймаута дочищаем процессы и говорим об этом прямо.
LEFTOVER="$(pgrep -f 're4\.exe' 2>/dev/null || true)"
if [ -n "$LEFTOVER" ]; then
    echo "игра пережила таймаут (pid $LEFTOVER) — убиваю, иначе следующий прогон"
    echo "пойдёт поверх неё и логи смешаются"
    pkill -f 're4\.exe' 2>/dev/null || true
    sleep 2
    pkill -f 'wineserver' 2>/dev/null || true
    sleep 1
    pgrep -f 're4\.exe' >/dev/null 2>&1 && pkill -9 -f 're4\.exe' 2>/dev/null || true
fi

echo
echo "код возврата: $RC"
echo -n "API по local_config.ini: "
grep -m1 '^Capability=' "$GAME_DIR/local_config.ini" 2>/dev/null || echo "(нет ключа)"

# Полный лог -> отфильтрованный (results/game-<mode>.log).
"$CURATE" "$RAW" "$LOG"

# Копия лога proton (PROTON_LOG=1) — в raw: у части запусков он не создаётся,
# поэтому копируем только существующие.
for f in /tmp/proton-"${USER:-armada}"/*.log; do
  [ -e "$f" ] && cp "$f" "$REPO/results/raw/proton-$MODE$SUFFIX-$(basename "$f")"
done

exit $RC
