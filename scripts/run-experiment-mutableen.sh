#!/bin/bash
# Эксперимент: умеет ли A740 (a7xx gen2) переинтерпретировать UBWC при
# смене формата, то есть работает ли бит MUTABLEEN.
#
# Правка ровно одна строка в patches/../experiments/:
#   ubwc_all_formats_compatible = True  в блоке a7xx_gen2
#
# Ожидаемый результат (измерен на RP6, A740, Mesa 26.2.3):
#   флаг выкл -> H memreq 0x100000, d1' (вид R32_UINT) EXACT
#   флаг вкл  -> H memreq 0x102000 (UBWC включился), d1' рассыпается
# Вывод: флаг на gen2 обязан оставаться false.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${WORK:-/var/home/armada/opencode/build}"
MESA="$WORK/mesa-rp6"
PATCH="$REPO/experiments/0001-experiment-ubwc-all-formats-compatible-a7xx-gen2.patch"

echo "### применяем экспериментальный патч"
cd "$MESA"
git apply "$PATCH"
grep -n "ubwc_all_formats_compatible" src/freedreno/common/freedreno_devices.py

echo "### пересборка драйвера (инкрементально: кодогенерация + линковка)"
podman run --rm -v "$MESA:/src:Z" -v "$WORK:/out:Z" \
    localhost/mesa-build-fedora44-full \
    bash -c 'cd /src && ninja -C build -j6 src/freedreno/vulkan/libvulkan_freedreno.so \
             && cp build/src/freedreno/vulkan/libvulkan_freedreno.so /out/out/'

echo "### положительный контроль: флаг попал в сгенерированную таблицу"
python3 - <<'PY'
import re
t = open('/var/home/armada/opencode/build/mesa-rp6/build/src/freedreno/common/freedreno_devices.h').read()
recs = dict(re.findall(r'static const struct fd_dev_info (__info\d+) = \{(.*?)\};', t, re.S))
idx = re.findall(r'\{[^}]*0x43050a01[^}]*\}, "FD740", &(__info\d+)', t)[0]
props = recs[idx][recs[idx].find('.props={'):recs[idx].find('},.magic')]
hit = [n for n, b in recs.items() if 'ubwc_all_formats_compatible=True' in b]
print(f"FD740 = {idx}; записей с флагом: {len(hit)} из {len(recs)}")
assert 'ubwc_all_formats_compatible=True' in props, "флаг НЕ попал - эксперим��нт не состоялся"
print("OK: флаг включён для нашего устройства")
PY

echo "### прогон теста с включённым флагом"
cd "$WORK"
./run-test.sh "$WORK/out/freedreno_icd.json" "$WORK/test-mutableen.log" >/dev/null 2>&1 || true
grep -E "^H: |^  H d1'|^H probe" "$WORK/test-mutableen.log"

echo "### откат и контрольный прогон (флаг выключен)"
cd "$MESA" && git checkout src/freedreno/common/freedreno_devices.py
podman run --rm -v "$MESA:/src:Z" -v "$WORK:/out:Z" \
    localhost/mesa-build-fedora44-full \
    bash -c 'cd /src && ninja -C build -j6 src/freedreno/vulkan/libvulkan_freedreno.so \
             && cp build/src/freedreno/vulkan/libvulkan_freedreno.so /out/out/'
cd "$WORK"
./run-test.sh "$WORK/out/freedreno_icd.json" "$WORK/test-patched.log" >/dev/null 2>&1 || true
grep -E "^H: |^  H d1'|^H probe" "$WORK/test-patched.log"
