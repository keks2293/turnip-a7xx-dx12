#!/bin/bash
# Сборка только turnip (Vulkan) из /src в /out. Запускается в контейнере.
#
# Две вещи, которые стоит не сломать (обе описаны в docs/analysis.md, раздел 6,
# и обе уже стоили сборки):
#   1. KMD обязан быть msm, а не kgsl. Путь перечисления для kgsl - это
#      open("/dev/kgsl-3d0") (tu_knl.cc), на RP6 этого узла нет, и
#      vkEnumeratePhysicalDevices молча возвращает 0 устройств.
#   2. Копировать надо в /out/out/, потому что /out смонтирован в корень build/,
#      а env.sh и манифест ICD указывают на build/out/. Копия в /out/ остаётся
#      невидимой для загрузчика, и результат выглядит как "патчи не применились".
set -euxo pipefail

cd /src
rm -rf build

meson setup build \
    --buildtype=release \
    --prefix=/out/usr \
    -Dopengl=false \
    -Dplatforms= \
    -Dgallium-drivers= \
    -Dvulkan-drivers=freedreno \
    -Dfreedreno-kmds=msm \
    -Dvulkan-layers= \
    -Dshared-glapi=disabled \
    -Dllvm=disabled \
    -Dvalgrind=disabled \
    -Dlibunwind=disabled \
    -Dbuild-tests=false

ninja -C build -j"$(nproc)" src/freedreno/vulkan/libvulkan_freedreno.so

SO=build/src/freedreno/vulkan/libvulkan_freedreno.so
# Диагностика KMD: у msm-сборки есть импорт drmGetVersion, у kgsl вместо него
# строки kgsl_3d0/kgsl_bo_init. Если nm недоступен - не роняем сборку, это
# только диагностика. Символ ищем без конвейера: grep -q завершается раньше
# nm, и под pipefail тот получает SIGPIPE, а пайплайн считается упавшим.
if UNDEF="$(nm -D --undefined-only "$SO" 2>/dev/null)"; then
    if [[ "$UNDEF" != *drmGetVersion* ]]; then
        echo "ВНИМАНИЕ: нет импорта drmGetVersion - похоже, собран kgsl-KMD, а не msm" >&2
        exit 1
    fi
else
    echo "ВНИМАНИЕ: nm недоступен, проверку KMD пропускаю" >&2
fi

mkdir -p /out/out
cp -v "$SO" /out/out/

# Манифест ICD пишется здесь, а не берётся из сборки: сгенерированный мезоном
# json содержит library_path install-пути (/out/usr/lib/...), который на
# устройстве не существует. Нужен путь, который загрузчик увидит на устройстве.
# По умолчанию - как в scripts/env.sh; переопределяется снаружи.
DEVICE_SO="${RP6_DEVICE_SO:-/var/home/armada/opencode/build/out/libvulkan_freedreno.so}"
cat > /out/out/freedreno_icd.json <<EOF
{
    "file_format_version": "1.0.0",
    "ICD": {
        "library_path": "$DEVICE_SO",
        "api_version": "1.3"
    }
}
EOF

ls -l /out/out/
