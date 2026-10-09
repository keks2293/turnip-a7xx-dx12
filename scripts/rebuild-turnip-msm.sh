#!/bin/bash
# Rebuild turnip with KMD=msm (the Mesa default). The RP6 device is a plain
# DRM node, /sys/class/kgsl is absent, so the kgsl KMD cannot find the GPU.
set -euxo pipefail

cd /src

# The option changes the set of TU_DEBUG/KMD sources but does not touch NIR/compiler —
# so reconfiguring the existing build directory is enough.
meson configure build -Dfreedreno-kmds=msm

ninja -C build -j"$(nproc)" src/freedreno/vulkan/libvulkan_freedreno.so

# /out is mounted at the root of build/, and the ICD manifest points at build/out/,
# so we copy there specifically — otherwise the loader would pick up the old binary.
mkdir -p /out/out
rm -f /out/out/libvulkan_freedreno.so
cp -v build/src/freedreno/vulkan/libvulkan_freedreno.so /out/out/
ls -l /out/out/libvulkan_freedreno.so
