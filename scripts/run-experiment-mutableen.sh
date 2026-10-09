#!/bin/bash
# Experiment: whether A740 (a7xx gen2) can reinterpret UBWC on a format change,
# i.e. whether the MUTABLEEN bit works.
#
# The change is exactly one line in patches/../experiments/:
#   ubwc_all_formats_compatible = True  in the a7xx_gen2 block
#
# Expected result (measured on RP6, A740, Mesa 26.2.3):
#   flag off -> H memreq 0x100000, d1' (view R32_UINT) EXACT
#   flag on  -> H memreq 0x102000 (UBWC kicked in), d1' breaks up
# Conclusion: the flag on gen2 must remain false.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${WORK:-/var/home/armada/opencode/build}"
MESA="$WORK/mesa-rp6"
PATCH="$REPO/experiments/0001-experiment-ubwc-all-formats-compatible-a7xx-gen2.patch"

echo "### applying the experimental patch"
cd "$MESA"
git apply "$PATCH"
grep -n "ubwc_all_formats_compatible" src/freedreno/common/freedreno_devices.py

echo "### rebuilding the driver (incremental: codegen + linking)"
podman run --rm -v "$MESA:/src:Z" -v "$WORK:/out:Z" \
    localhost/mesa-build-fedora44-full \
    bash -c 'cd /src && ninja -C build -j"$(nproc)" src/freedreno/vulkan/libvulkan_freedreno.so \
             && cp build/src/freedreno/vulkan/libvulkan_freedreno.so /out/out/'

echo "### positive control: the flag made it into the generated table"
python3 - <<'PY'
import re
t = open('/var/home/armada/opencode/build/mesa-rp6/build/src/freedreno/common/freedreno_devices.h').read()
recs = dict(re.findall(r'static const struct fd_dev_info (__info\d+) = \{(.*?)\};', t, re.S))
idx = re.findall(r'\{[^}]*0x43050a01[^}]*\}, "FD740", &(__info\d+)', t)[0]
props = recs[idx][recs[idx].find('.props={'):recs[idx].find('},.magic')]
hit = [n for n, b in recs.items() if 'ubwc_all_formats_compatible=True' in b]
print(f"FD740 = {idx}; entries with the flag: {len(hit)} of {len(recs)}")
assert 'ubwc_all_formats_compatible=True' in props, "flag did NOT make it - the experiment did not happen"
print("OK: the flag is enabled for our device")
PY

echo "### running the test with the flag enabled"
cd "$WORK"
./run-test.sh "$WORK/out/freedreno_icd.json" "$WORK/test-mutableen.log" >/dev/null 2>&1 || true
grep -E "^H: |^  H d1'|^H probe" "$WORK/test-mutableen.log"

echo "### rollback and control run (flag disabled)"
cd "$MESA" && git checkout src/freedreno/common/freedreno_devices.py
podman run --rm -v "$MESA:/src:Z" -v "$WORK:/out:Z" \
    localhost/mesa-build-fedora44-full \
    bash -c 'cd /src && ninja -C build -j"$(nproc)" src/freedreno/vulkan/libvulkan_freedreno.so \
             && cp build/src/freedreno/vulkan/libvulkan_freedreno.so /out/out/'
cd "$WORK"
./run-test.sh "$WORK/out/freedreno_icd.json" "$WORK/test-patched.log" >/dev/null 2>&1 || true
grep -E "^H: |^  H d1'|^H probe" "$WORK/test-patched.log"
