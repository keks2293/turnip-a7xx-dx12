#!/usr/bin/env bash
# Запуск Starfield (DX12, RUNE-репак) на патченом или стоковом turnip.
#
#   scripts/run-starfield.sh [stock|patched] [timeout-sec]   # по умолчанию patched, 0 = без таймаута
#
# Wine в системе НЕ установлен (rpm -q wine → нет), поэтому, как и RE4/WNO,
# игра идёт напрямую через proton-cachyos. Запуск из Heroic невозможен: игры
# нет в sideload-библиотеке, а лаунчерные конфиги не подсовывают
# VK_DRIVER_FILES, то есть патченый драйвер не подхватится (docs/analysis.md §11).
#
# Префикс создаётся заново (ранее на устройстве префикса Starfield не было):
# имя обязано содержать цифры — protonfixes вытаскивает номер игры из
# STEAM_COMPAT_DATA_PATH (re.findall(r'\d+')).
#
# CWD обязан быть папкой игры: репак ищет данные относительно текущего
# каталога. Проверка «пропажи моделей» — визуальная, окно появится на экране:
# лог фиксирует только здоровье стека (устройство, swapchain, ошибки Vulkan).
set -euo pipefail

MODE="${1:-patched}"
TIMEOUT="${2:-0}"

GAME_DIR="/run/media/armada/Z Slim/Games/Starfield.Digital.Premium.Edition-InsaneRamZes"
GAME_EXE="$GAME_DIR/Starfield.exe"
PREFIX="/var/home/armada/Games/Heroic/Prefixes/starfield1716740"
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

[ -x "$PROTON" ] || { echo "нет proton: $PROTON" >&2; exit 1; }
[ -f "$GAME_EXE" ] || { echo "нет игры: $GAME_EXE" >&2; exit 1; }
mkdir -p "$PREFIX"   # proton кладёт pfx.lock только если каталог уже есть

# Ловушка build-turnip.sh №3: сборка без -Dplatforms=x11,wayland даёт драйвер
# без VK_KHR_xcb_surface — wine не переводит win32-поверхность, и DXVK падает
# на vkCreateInstance за секунду до картинки (лог: "Failed to create Vulkan
# instance"). Ловим сразу, а не таймаутом в 600 с.
# Вывод копим целиком: под set -o pipefail ранний выход grep -q рвёт трубу,
# vulkaninfo ловит SIGPIPE, и пайплайн падает ложно.
if ! grep -q 'VK_KHR_xcb_surface' <<<"$(vulkaninfo 2>/dev/null)"; then
    echo "нет VK_KHR_xcb_surface: драйвер собран без -Dplatforms=x11,wayland" >&2
    echo "(см. build-turnip.sh, ловушка №3; пересборка scripts/build-turnip.sh)" >&2
    exit 1
fi

# vkd3d-proton: в proton-cachyos старая версия без фикса «fp16 on Turnip» —
# Turnip честно не умеет shaderDenormPreserveFloat16 (гейт A8XX), vkd3d из-за
# этого отказывает в fp16-шейдере Starfield (compute PSO → hr 0x80070057) и
# игра падает после компиляции шейдеров. В master vkd3d-proton починено
# (allow non-compliant denorm), но релиза нет — собираем сами:
#   /var/home/armada/opencode/build/vkd3d-proton/build.sh
# и кладём dll в ПАПКУ ИГРЫ: proton при каждом запуске перезаписывает свои
# копии в system32 префикса (проверено по sha256), а exe-каталог первым идёт
# в поиске нативных dll. Подмена — только здесь (WINEDLLOVERRIDES), RE4 со
# своим префиксом не затрагивается; /usr не трогаем.
VKD3D_DLL="$GAME_DIR/d3d12.dll"
if [ -r "$VKD3D_DLL" ]; then
  export WINEDLLOVERRIDES="d3d12,d3d12core=n"
fi

mkdir -p "$REPO/results/raw" "$REPO/results/starfield"
TS="$(date +%Y%m%d-%H%M%S)"
RAW="$REPO/results/raw/starfield-$MODE-$TS.log"
LOG="$REPO/results/starfield/starfield-$MODE-$TS.log"
CURATE="$REPO/scripts/curate-game-log.sh"

export STEAM_COMPAT_DATA_PATH="$PREFIX"
export STEAM_COMPAT_CLIENT_INSTALL_PATH="/var/home/armada/.local/share/Steam"
export STEAM_COMPAT_APP_ID=1716740   # Starfield; нужен protonfixes для фиксов
export PROTON_LOG=1
export WINEDEBUG="${WINEDEBUG:--all}"   # для диагностики: WINEDEBUG=+vulkan ./scripts/...
export VK_LOADER_DEBUG="${VK_LOADER_DEBUG:-error,warn}"

# Диагностические пасsthrough (dgc-plan §9.9):
#   VKD3D_DISABLE_EXTENSIONS=VK_EXT_device_generated_commands — A/B без DGC
#   (vkd3d device.c:202, список через запятую)
#   DGC_DUMP=1 — дамп DGC-токенов layout'а в stderr (tu_dgc.cc)
#   MESA_DEBUG=1 — mesa_log-вывод драйвера (в release-сборке vk_errorf
#   дополнительно требует debug-колбэк — см. dgc-plan §9.9)
for name in VKD3D_DISABLE_EXTENSIONS MESA_DEBUG DGC_DUMP; do
    if [ -n "${!name:-}" ]; then export "$name"; fi
done

{
  echo "# date: $(date -Is)"
  echo "# mode: $MODE"
  echo "# VK_DRIVER_FILES=${VK_DRIVER_FILES:-<unset>}"
  [ -r "$VKD3D_DLL" ] && \
    echo "# vkd3d-proton: master-сборка $(sha256sum "$VKD3D_DLL" | cut -c1-16) (native-override)" || \
    echo "# vkd3d-proton: из proton-cachyos (без фикса fp16)"
  echo "# devices (name | sparseResidencyImage2D):"
  vulkaninfo 2>/dev/null | awk '
    /deviceName/ { dev=$3" "$4" "$5" "$6" "$7 }
    /sparseResidencyImage2D/ { print "#   " dev " | " $3; dev="" }
  '
  echo "# ---"
} > "$RAW"

# Переживший таймаут wine мешает следующему прогону (см. docs/analysis.md §12):
# дочищаем до старта и после.
pkill -f 'Starfield\.exe' 2>/dev/null || true
sleep 1

cd "$GAME_DIR"
set +e
read -r -a EXTRA <<< "${GAMEARGS:-}"
if [ "$TIMEOUT" -gt 0 ] 2>/dev/null; then
  timeout --foreground --signal=INT "$TIMEOUT" \
    "$PROTON" waitforexitandrun "$GAME_EXE" ${EXTRA[@]+"${EXTRA[@]}"} >>"$RAW" 2>&1
else
  "$PROTON" waitforexitandrun "$GAME_EXE" ${EXTRA[@]+"${EXTRA[@]}"} >>"$RAW" 2>&1
fi
RC=$?
set -e

LEFTOVER="$(pgrep -f 'Starfield\.exe' 2>/dev/null || true)"
if [ -n "$LEFTOVER" ]; then
  echo "игра пережила таймаут (pid $LEFTOVER) — убиваю, иначе логи смешаются"
  pkill -f 'Starfield\.exe' 2>/dev/null || true
  sleep 2
  pkill -f 'wineserver' 2>/dev/null || true
  sleep 1
  pgrep -f 'Starfield\.exe' >/dev/null 2>&1 && pkill -9 -f 'Starfield\.exe' 2>/dev/null || true
fi

echo "код возврата: $RC"
"$CURATE" "$RAW" "$LOG"
echo "лог: $LOG  (полный: $RAW)"
for f in /tmp/proton-"${USER:-armada}"/*.log; do
  [ -e "$f" ] && cp "$f" "$REPO/results/raw/proton-starfield-$MODE-$(basename "$f")"
done
exit $RC
