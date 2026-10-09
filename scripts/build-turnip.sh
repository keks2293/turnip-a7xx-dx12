#!/bin/bash
# Build of turnip (Vulkan) only, from /src into /out. Runs in a container.
#
# Three things not to break (described in docs/analysis.md, sections 6 and 9):
#   1. The KMD must be msm, not kgsl. The enumeration path for kgsl is
#      open("/dev/kgsl-3d0") (tu_knl.cc); on RP6 this node does not exist, and
#      vkEnumeratePhysicalDevices silently returns 0 devices.
#   2. The copy must go into /out/out/, because /out is mounted at the root of build/,
#      and env.sh and the ICD manifest point to build/out/. A copy in /out/ stays
#      invisible to the loader, and the result looks like "the patches were not applied".
#   3. -Dplatforms= must not be left empty. An empty build is fine for
#      our own test (it does not show windows), but then the driver has neither
#      VK_KHR_xcb_surface nor VK_KHR_wayland_surface. winevulkan translates
#      the game's win32 surface into a host surface, and without these extensions
#      DXVK crashes at vkCreateInstance: "Failed to create Vulkan instance".
#      The full set is needed, as in the batch image build: -Dplatforms=x11,wayland.
#   4. --prefix must be /usr. From prefix+datadir meson derives -DDATADIR,
#      and its only use in the driver is the path to driconf
#      (src/util/xmlconfig.c:1375, parseConfigDir(DATADIR "/drirc.d")).
#      With --prefix=/out/usr the driver looked for the config in /out/usr/share/drirc.d,
#      which does not exist on the device, and read no turnip options at all.
#      In particular tu_enable_softfloat32 was lost, and without it
#      shaderDenormPreserveFloat32 = false, vkd3d does not raise SM 6.6, and a
#      DX12 game rejects with the window "your GPU was not supported" at 12_0.
#      Checked with a line in the binary: grep -a -o '/usr/share/drirc\.d'.
set -euxo pipefail

cd /src
rm -rf build

# Device memory is shared with the GPU (7.3 GB total), so the build cannot be run
# at the same time as the game: together they drive the system into OOM, the cursor
# freezes and a reboot is needed. This check runs before the start so as not to
# hit that later.
if pgrep -f 're4\.exe|wine|wineserver' >/dev/null 2>&1; then
    echo "WARNING: Wine/the game is already running. Build the driver when the game is closed." >&2
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
# KMD diagnostics: the msm build has the drmGetVersion import, while kgsl has
# kgsl_3d0/kgsl_bo_init strings instead. If nm is unavailable - do not fail the
# build, this is diagnostics only. The symbol is searched without a pipeline: grep -q
# finishes before nm, and under pipefail nm gets SIGPIPE and the pipeline is
# considered failed.
if UNDEF="$(nm -D --undefined-only "$SO" 2>/dev/null)"; then
    if [[ "$UNDEF" != *drmGetVersion* ]]; then
        echo "WARNING: no drmGetVersion import - looks like the kgsl KMD was built, not msm" >&2
        exit 1
    fi
else
    echo "WARNING: nm unavailable, skipping the KMD check" >&2
fi

mkdir -p /out/out
cp -v "$SO" /out/out/

# The ICD manifest is written here rather than taken from the build: the meson
# generated json contains install paths for library_path (/out/usr/lib/...), which do
# not exist on the device. A path the loader will see on the device is needed.
# By default - as in scripts/env.sh; overridable from outside.
DEVICE_SO="${RP6_DEVICE_SO:-/var/home/armada/opencode/build/out/libvulkan_freedreno.so}"
# api_version and file_format_version - as in the packaged driver from the image
# (mesa-vulkan-drivers). It used to be 1.4.341 here, but the packaged manifest on
# the device (/usr/share/vulkan/icd.d/freedreno_icd.aarch64.json) declares
# 1.4.354 - exactly the same version as the driver itself (vulkaninfo: apiVersion =
# 1.4.354). There is no need to lower api_version to "1.3": DXVK asks for
# VK_API_VERSION_1_3 itself.
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
