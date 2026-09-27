#!/bin/bash
# Сборка только turnip (Vulkan) из /src в /out. Запускается в контейнере.
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
    -Dfreedreno-kmds=kgsl \
    -Dvulkan-layers= \
    -Dshared-glapi=disabled \
    -Dllvm=disabled \
    -Dvalgrind=disabled \
    -Dlibunwind=disabled \
    -Dbuild-tests=false

ninja -C build -j4 src/freedreno/vulkan/libvulkan_freedreno.so

cp -v build/src/freedreno/vulkan/libvulkan_freedreno.so /out/
ls -l /out/
