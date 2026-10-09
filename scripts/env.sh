# Paths needed to run the test on the patched turnip.
# /usr must not be touched (docs/analysis.md, section 7), and sudo on the device
# requires a password, so the driver is handed to the Vulkan loader via
# an environment variable, without bind-mount.

# ICD manifest: points the loader at our .so instead of the system one.
export VK_DRIVER_FILES=/var/home/armada/opencode/build/out/freedreno_icd.json

# Built driver and manifest:
export RP6_TURNIP_SO=/var/home/armada/opencode/build/out/libvulkan_freedreno.so
export RP6_TURNIP_ICD=/var/home/armada/opencode/build/out/freedreno_icd.json

# Sources with the patches applied and the patches from this same repository:
export RP6_MESA_SRC=/var/home/armada/opencode/build/mesa-rp6
export RP6_PATCHES=/var/home/armada/opencode/turnip-a7xx-dx12/patches

# Test:
export RP6_TEST_SRC=/var/home/armada/opencode/turnip-a7xx-dx12/tests
export RP6_TEST_BIN=/var/home/armada/opencode/build/rp6-vkd3d-sparse-test
