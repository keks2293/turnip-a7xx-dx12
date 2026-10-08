#!/bin/bash
# Сборка rp6-vkd3d-sparse-test в контейнере (на устройстве нет компилятора).
set -euxo pipefail

dnf -y install gcc vulkan-headers vulkan-loader-devel

cd /work
gcc -O2 -o /out/rp6-vkd3d-sparse-test rp6-vkd3d-sparse-test.c -lvulkan
ls -l /out/rp6-vkd3d-sparse-test
