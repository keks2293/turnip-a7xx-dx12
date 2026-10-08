#!/usr/bin/env bash
# Запуск RE4 (DX12) через proton-cachyos на стоковом или патченом turnip.
#
# Игра не зарегистрирована в Steam, префикс создат��н Heroic (sideload), поэтому
# запуск идёт напрямую через proton из /usr/share/steam/compatibilitytools.d.
#
#   scripts/run-game.sh patched [sec]   # патченый драйвер из build/out
#   scripts/run-game.sh stock           # пакетный драйвер из /usr/lib64
#
# Драйвер подсовывается загрузчику через VK_DRIVER_FILES: /usr трогать нельзя,
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

[ -x "$PROTON" ] || { echo "нет proton: $PROTON" >&2; exit 1; }
[ -f "$GAME_EXE" ] || { echo "нет игры: $GAME_EXE" >&2; exit 1; }
[ -d "$PREFIX" ]  || { echo "нет префикса: $PREFIX" >&2; exit 1; }

# Полный лог прогона (raw) — в results/raw, он в .gitignore: там тысячи строк
# шума wine/FEX, которые не говорят ничего о причине. В git идёт отфильтрованный
# results/game-<mode>.log, собранный scripts/curate-game-log.sh.
mkdir -p "$REPO/results/raw"
RAW="$REPO/results/raw/game-$MODE.log"
LOG="$REPO/results/game-$MODE.log"
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
timeout --foreground --signal=INT "$TIMEOUT" \
  "$PROTON" waitforexitandrun "$GAME_EXE" >>"$RAW" 2>&1
RC=$?
set -e

echo
echo "код возврата: $RC"

# Полный лог -> отфильтрованный (results/game-<mode>.log).
"$CURATE" "$RAW" "$LOG"

# Копия лога proton (PROTON_LOG=1) — в raw: у части запусков он не создаётся,
# поэтому копируем только существующие.
for f in /tmp/proton-"${USER:-armada}"/*.log; do
  [ -e "$f" ] && cp "$f" "$REPO/results/raw/proton-$MODE-$(basename "$f")"
done

exit $RC
