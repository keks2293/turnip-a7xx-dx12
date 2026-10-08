# Пути, нужные для прогона теста на правленом turnip.
# /usr трогать нельзя (docs/analysis.md, раздел 7), а sudo на устройстве
# требует пароль, поэтому драйвер подсовывается загрузчику Vulkan через
# переменную окружения, без bind-mount.

# Манифест ICD: указывает загрузчику наш .so вместо системного.
export VK_DRIVER_FILES=/var/home/armada/opencode/build/out/freedreno_icd.json

# Собранный драйвер и манифест:
export RP6_TURNIP_SO=/var/home/armada/opencode/build/out/libvulkan_freedreno.so
export RP6_TURNIP_ICD=/var/home/armada/opencode/build/out/freedreno_icd.json

# Исходники с наложенными патчами и патчи этого же репозитория:
export RP6_MESA_SRC=/var/home/armada/opencode/build/mesa-rp6
export RP6_PATCHES=/var/home/armada/opencode/turnip-a7xx-dx12/patches

# Тест:
export RP6_TEST_SRC=/var/home/armada/opencode/turnip-a7xx-dx12/tests
export RP6_TEST_BIN=/var/home/armada/opencode/build/rp6-vkd3d-sparse-test
