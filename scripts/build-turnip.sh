#!/bin/bash
# Сборка только turnip (Vulkan) из /src в /out. Запускается в контейнере.
#
# Три вещи, которые стоит не сломать (описаны в docs/analysis.md, разделы 6 и 9):
#   1. KMD обязан быть msm, а не kgsl. Путь перечисления для kgsl - это
#      open("/dev/kgsl-3d0") (tu_knl.cc), на RP6 этого узла нет, и
#      vkEnumeratePhysicalDevices молча возвращает 0 устройств.
#   2. Копировать надо в /out/out/, потому что /out смонтирован в корень build/,
#      а env.sh и манифест ICD указывают на build/out/. Копия в /out/ остаётся
#      невидимой для загрузчика, и результат выглядит как "патчи не применились".
#   3. -Dplatforms= нельзя оставлять пустым. Пустая сборка годится для
#      собственного теста (он не показывает окна), но в драйвере тогда нет ни
#      VK_KHR_xcb_surface, ни VK_KHR_wayland_surface. winevulkan переводит
#      win32-поверхность игры в поверхность хоста, и без этих расширений
#      DXVK падает на vkCreateInstance: "Failed to create Vulkan instance".
#      Нужен полный набор, как в пакетной сборке образа: -Dplatforms=x11,wayland.
#   4. --prefix обязан быть /usr. Из prefix+datadir мeson выводит -DDATADIR,
#      а единственное его использование в драйвере - путь к driconf
#      (src/util/xmlconfig.c:1375, parseConfigDir(DATADIR "/drirc.d")).
#      С --prefix=/out/usr драйвер искал конфиг в /out/usr/share/drirc.d,
#      которого на устройстве нет, и не читал ни одной опции turnip.
#      В частности терялась tu_enable_softfloat32, а без неё
#      shaderDenormPreserveFloat32 = false, vkd3d не поднимает SM 6.6, и
#      DX12-игра отказывает окном "your GPU was not supported" на 12_0.
#      Проверяется строкой в бинарнике: grep -a -o '/usr/share/drirc\.d'.
set -euxo pipefail

cd /src
rm -rf build

# Память устройства общая с GPU (7.3 ГБ всего), поэтому сборку нельзя запускать
# одновременно с игрой: вместе они уводят систему в OOM, курсор замирает и
# нужна перезагрузка. Проверка перед стартом - чтобы не поймать это позже.
if pgrep -f 're4\.exe|wine|wineserver' >/dev/null 2>&1; then
    echo "ВНИМАНИЕ: Wine/игра уже запущены. Собери драйвер, когда игра закрыта." >&2
    exit 1
fi

meson setup build \
    --buildtype=release \
    --prefix=/usr \
    -Dopengl=false \
    -Dplatforms=x11,wayland \
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
# api_version и file_format_version - как у пакетного драйвера образа
# (mesa-vulkan-drivers). Раньше здесь стояло 1.4.341, но пакетный манифест на
# устройстве (/usr/share/vulkan/icd.d/freedreno_icd.aarch64.json) объявляет
# 1.4.354 - ровно ту же версию, что и сам драйвер (vulkaninfo: apiVersion =
# 1.4.354). Занижать api_version до "1.3" не нужно: DXVK сам просит
# VK_API_VERSION_1_3.
cat > /out/out/freedreno_icd.json <<EOF
{
    "file_format_version": "1.0.1",
    "ICD": {
        "library_path": "$DEVICE_SO",
        "api_version": "1.4.354",
        "library_arch": "64"
    }
}
EOF

ls -l /out/out/
