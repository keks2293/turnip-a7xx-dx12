#!/usr/bin/env bash
# Сжимает полный лог прогона игры (raw) до значимых строк.
#
# Полный лог — это stdout+stderr proton/wine: тысячи строк вида
#   D 138 Load module comctl32.dll ...            (загрузка DLL)
#   D 168 Exception: Code: C0000005 ...            (FEX гоняет x86-код)
#   1262.1:...:warn:vkd3d-proton:...does not exist (промах кеша шейдеров)
# Они не несут информации о том, почему игра не запустилась, и перекрывают то,
# что несёт: отказ vkd3d по feature level, создание и пересоздание swapchain,
# отказ DXVK на vkCreateInstance.
#
#   scripts/curate-game-log.sh <raw.log> [out.log]   # по умолчанию в stdout
set -euo pipefail

IN="${1:?usage: curate-game-log.sh <raw.log> [out.log]}"
[ -r "$IN" ] || { echo "нет файла: $IN" >&2; exit 1; }

grep -vE \
  -e 'Load module ' \
  -e '^[A-Z] [0-9A-Fa-f]+ (Exception: Code|Handled self-modifying code|Call-ret stack inbalance|Passing through exception|Loaded volatile metadata|Add (image )?SMC interval|We don'"'"'t support modifying)' \
  -e '^I [0-9A-Fa-f]+ FEX:' \
  -e '^(fixme|trace):' \
  "$IN" \
| awk '
    # Промахи кеша шейдеров: строк тысячи, а различие только в имени
    # пайплайна. Считаем их и в конце выдаём итоговое число.
    /d3d12_pipeline_library_load_pipeline: Pipeline .* does not exist\.$/ { n++; next }
    { print }
    END {
      if (n)
        printf "# [свёрнуто %d строк: d3d12_pipeline_library_load_pipeline ... does not exist]\n", n
    }
  ' \
> "${2:-/dev/stdout}"
