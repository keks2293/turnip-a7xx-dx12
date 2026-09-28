#!/bin/bash
# Пересборка turnip с KMD=msm (дефолт Mesa). Устройство RP6 — обычный
# DRM-узел, /sys/class/kgsl отсутствует, поэтому kgsl-KMD не находит GPU.
set -euxo pipefail

cd /src

# Опция меняет набор TU_DEBUG/KMD-исходников, но не трогает NIR/compiler —
# поэтому хватит переконфигурации существующего build-каталога.
meson configure build -Dfreedreno-kmds=msm

ninja -C build -j"$(nproc)" src/freedreno/vulkan/libvulkan_freedreno.so

# /out смонтирован в корень build/, а манифест ICD указывает на build/out/,
# поэтому копируем именно туда — иначе загрузчик подхватит старый бинарник.
mkdir -p /out/out
rm -f /out/out/libvulkan_freedreno.so
cp -v build/src/freedreno/vulkan/libvulkan_freedreno.so /out/out/
ls -l /out/out/libvulkan_freedreno.so
