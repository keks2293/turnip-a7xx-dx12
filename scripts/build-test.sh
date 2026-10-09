#!/bin/bash
# Build rp6-vkd3d-sparse-test in the container (there is no compiler on the device).
set -euxo pipefail

dnf -y install gcc vulkan-headers vulkan-loader-devel

cd /work
gcc -O2 -Wall -o /out/rp6-vkd3d-sparse-test rp6-vkd3d-sparse-test.c -lvulkan
ls -l /out/rp6-vkd3d-sparse-test
