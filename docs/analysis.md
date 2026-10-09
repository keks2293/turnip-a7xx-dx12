# RP6: why DX12 games don't reach feature level 12_0

**Device:** Retroid Pocket 6 — QCS8550, Adreno **A740** (gen2, not A750)
**Build image:** Armada main — Mesa 26.2.3, kernel 7.2.6, CachyOS Proton 11
**Measurements:** stock Mesa 26.2.2, 20–21.09.2026

## 1. Symptom

Under Proton 11, DX12 games don't get past D3D12 feature level 11_1: vkd3d
does not expose `TiledResourcesTier` (it stays `NOT_SUPPORTED`), and the 12_0
gate does not pass. By the driver code it "should get there".

## 2. Cause

Turnip exports the `sparseResidencyImage2D` feature only when two conditions
hold:

- `has_sparse_prr` — PRR is supported by the hardware and the kernel; it works
  on the RP6;
- `ubwc_all_formats_compatible` — "a UBWC image can be reinterpreted into a
  different format"; the driver claims this **only for gen3 / A750+**, on gen2
  it was never checked.

A740 is gen2, so the second condition is always false. The feature is not
exported, vkd3d doesn't hand out TILED, and 12_0 becomes unreachable.

The obstacle is exactly one thing — exposing the feature. The hardware and
kernel are fine: VM_BIND and PRR exist on the RP6, the driver detects and uses
them. On the sparse path Turnip disables UBWC entirely, so format casting is
impossible there and the gate is redundant.

Removing the gate is a single line: drop `&& ubwc_all_formats_compatible` from
the exposure condition. On gen2 this flag is always false anyway, so the gate
was the only obstacle there, while on gen3 and newer it stays in force.

The exposure condition **does not depend on the chip**: after the edit
`sparseResidencyImage2D = has_sparse_prr`, and PRR comes from the kernel
(`MSM_PARAM_HAS_PRR` for drm/msm, `kgsl_is_virtual_bo_supported` for kgsl).
Therefore the gate is removed identically for gen1 (FD725/FD730) and for a6xx
(Adreno 640 and neighbors) — `ubwc_all_formats_compatible` is not set for them
either, so the stock driver didn't hand them the feature. From there everything
is decided by vkd3d: the steps of
`d3d12_device_determine_tiled_resources_tier` (`device.c:8898`) check only the
features and sparse properties that turnip exposes the same way for all chips
(`tu_device.cc:1321-1325`), so with PRR present the result is the same
everywhere — `TIER_2` (the third step runs into the lack of 3D sparsity) and
feature level 12_0 is reachable. This was not tested on FD730/Adreno 640:
neither the kernel (needs a fresh msm drm with `MSM_PARAM_HAS_PRR`) nor a run.

Turning the flag itself on in `a7xx_gen2` is worse, and this is now a
measurement, not a hypothesis. The flag denotes a specific hardware mechanism:
the driver sets the `MUTABLEEN` bit in the texture descriptor, and the hardware
with it must correctly reinterpret UBWC when the format changes. Upstream
introduced it only for A750+ ("A750+ added a special flag"), although the bit
in the register map is declared for a7xx as a whole, and nobody has tested A740
in this respect. The check has been done — **A740 can't do this**, see below.
The Vulkan spec says nothing about UBWC: it is Adreno proprietary compression,
and in API terms such a claim cannot be expressed — it had to be checked by
behavior.

### On what grounds `False` for A740

This deserves separate treatment, because `False` is easy to mistake for a
tested result. Checked against the sources and the generated header
`freedreno_devices.h` (33 `__info*` records):

- the property is declared as a plain `bool ubwc_all_formats_compatible;`
  (`freedreno_dev_info.h:408`) with no initializer, so unset fields equal
  `false` — the codegen enumerates only explicitly set properties;
- in `freedreno_devices.py` the property occurs **exactly once** — line 878,
  in the `a7xx_gen3` block. Neither `a7xx_base` nor `a7xx_gen2` set it;
- in the generated header the flag is present in 7 of the 33 records, and our
  `FD740` (`__info23`, chip-id `0x43050a01`) is among those without it;
- confirmed by measurement: the stock driver reports
  `sparseResidencyImage2D=0`, the patched one — `1`.

So `False` is **the absence of the flag, not a test result**. The grounds for
`True` come only from the upstream comment: "A750+ added a special flag that
allows HW to correctly interpret UBWC, including UBWC fast-clear when casting
image to a different format permitted by Vulkan" — i.e. knowledge about A750,
extrapolated back to the whole a7xx line. Meanwhile the `MUTABLEEN` bit in the
register map is marked `variants="A7XX-"`, i.e. the position is declared for
all a7xx including gen2: **declared does not mean it works**.

### Check on A740: MUTABLEEN does not work

The question is settled by two experiments. The first turns the flag on and
shows that MUTABLEEN breaks reads; the second isolates the cause, because the
first changed two variables at once.

**Experiment 1** — one line `ubwc_all_formats_compatible = True` in the
`a7xx_gen2` block. The rebuild is incremental (codegen + linking), and in the
generated header the flag is confirmed for records 22–32 (was 26–32), i.e. it
also turned on for our `__info23` (FD740).

Probe **H** is the only one that can reach MUTABLEEN at all: a non-sparse
image + `MUTABLE` with the list `{RGBA8, R32_UINT}`, and those two formats have
different `fd6_ubwc_compat_type`. All sparse probes (B, E, F, S2) don't engage
MUTABLEEN: on the sparse path `ubwc_possible()` is always false, UBWC is off
there regardless of the flag.

| | flag off (upstream) | flag on |
|---|---|---|
| `H` memreq | `0x100000` — no UBWC metadata | `0x102000` — **UBWC turned on** |
| `H d2'` — 8888 view (same class as base) | `TRANSFORMED` | `TRANSFORMED` — **unchanged** |
| `H d1'` — R32_UINT view (different class) | 0 mismatches, **EXACT** | 8 mismatches, garbage |

`memreq` grew to `0x102000` — so the `is_mutable = true` branch really was
taken and UBWC really turned on, rather than "the flag changed nothing". A view
of the same class as the base format (`d2'`) reads byte-identically: MUTABLEEN
does not participate in this read, and this is a control showing the problem is
not a general breakage of compression. But a view of a different class
(`d1'`, R32_UINT over RGBA8) — exactly the one whose reading MUTABLEEN
regulates — fell apart.

**But the conclusion about MUTABLEEN by itself did not follow from this.** Two
things changed at once: the MUTABLEEN bit and the composition of the list
(cross-class versus single-class). "Something else turned on" is a legitimate
objection.

**Experiment 2** separates them. From the code it is visible that
`is_mutable = true` sits in exactly one branch — the `else` of
`!mutable_ubwc_fc` — and that whole branch lies inside
`if (!tu6_mutable_format_list_ubwc_compatible(...))`. So for probes T and S
(lists 0006-compatible) the block is not executed at all, and MUTABLEEN is
**not set** there, even though UBWC is on and reads are exact. This already
shows that UBWC on a mutable image works by itself, and the suspicion "something
else turned on" is half-dispelled. To close it entirely, MUTABLEEN is forced
unconditionally for all mutable non-depth images — **with the flag off** — so
that no new format list is allowed and no probe changes its layout.

The result — and it is stronger than what the first experiment gave:

| probe (list **the same** as 0006) | without MUTABLEEN | MUTABLEEN forced |
|---|---|---|
| `T-tr` (BGRA8_UNORM/BGRA8_UINT, UBWC) | **0/262144** | **261120/262144** |
| `S-tr` (BGRA8, sparse) | **0/16384** | **16384/16384** |
| `H` memreq / `d1'` | `0x100000` / EXACT | `0x100000` / EXACT — **unchanged** |

`H` didn't move by a single bit — so the variable "list composition / layout /
other inclusions" is indeed ruled out, and what breaks is **MUTABLEEN itself**.

Moreover it breaks not only on cross-class pairs but also on exactly the
BGRA8_UNORM/BGRA8_UINT pair that 0006 relies on. The character of the
corruption is telling:

```
t-uint y0 x0: read 0x000000a5 want 0x00a50000
S-tr  (0, 0):  got  0x00a50000 want 0x000000a5
```

What was read is **exactly what was written** (`fill2[0] = 0x000000a5`), but the
B↔R channel permutation disappeared, i.e. `want` and `read` swapped places. The
data is not garbage — the byte order is broken. This is precisely the mechanism
described by the upstream comment in `tu_clear_blit.cc:5547`: "the attachment in
GMEM ignores the swap except when MUTABLEEN is enabled".

**Conclusion: `ubwc_all_formats_compatible` on gen2 must stay `false`, and this
is now a measurement, not conservatism.** A740 does not reinterpret the
programmable format with MUTABLEEN: the data is preserved, but the channels end
up in the wrong place. Hence the second, more important consequence for the
patches: **MUTABLEEN must not be turned on on gen2 with any list**, therefore
0006 must solve the problem by merging types rather than by allowing the flag —
and that is how it is done. Checked separately: in the patched tree `is_mutable`
is assigned `true` in exactly one line, and it sits inside the branch that 0006
makes unreachable for compatible lists; `T-tr` and `S-tr` give 0 mismatches.

Changes in probes with the flag turned on are explained as follows: B, E, F and
S2 started to be created (gate 0004 is bypassed by the first clause of the
predicate), and they read correctly at that — `0x37` in the bound tile, zeros
outside it — because the sparse path is tiled even without UBWC, and the
linear+sparse hole does not occur there. So these changes are harmless; the
harm is concentrated in non-sparse MUTABLEEN.

Run logs: `results/test-mutableen.log` (experiment 1),
`results/test-expC.log` (experiment 2), `results/test-patched.log` (the
reference — any clean rebuild matches it byte for byte). The experiment patches
are in `experiments/`; they are **not intended for commit** and are not part of
0004–0006.

### Applicability scope of 0004 — gen1 and gen2

The flag exists on `a7xx_gen3`, hence on A750 and all a8xx (810, 829, **830**,
840, X2-85/90). On them `sparseResidencyImage2D` is exported by the stock
driver, and 0004 there is not just redundant but unreachable: even with a NULL
list the mutable image stays tiled, because when `mutable_ubwc_fc == true` the
branch sets `is_mutable = true` and only clears UBWC, without touching
`force_linear_tile`. The linear+sparse hole does not occur on gen3, hence the
rejection on create doesn't trigger either.

Gen1 is FD725/FD730, gen2 is X1-45/FD735, **FD740 + X1-85 (ours)**, FDA32,
**FD740v3 (Quest 3)** (`docs/a7xx-gen-props.md`, binding table). None of them
has the flag — previously FD730 was mistakenly listed here as gen2 — so the
rejection on create is needed on all chips outside gen3.

For removing the gate this question isn't even needed. On the sparse path UBWC
is disabled unconditionally: `ubwc_possible()` returns false for
`SPARSE_RESIDENCY`. It was assumed that the reason was only leftovers of
fast-clear — that's half-truth; the real reason is structural, see below. In
any case the gate checks a capability that the sparse path doesn't use anyway —
removing it is safe regardless of the answer about A740.

## 2.1. About UBWC: where it gets turned off and why these are different things

UBWC on A740 **is enabled for regular images**, and this is measured, not
assumed. Probe G (512x512 RGBA8, tiled, non-sparse, non-mutable) gives
`memreq 0x102000` versus `0x100000` for the LINEAR control — i.e. compression
metadata is present in the layout. Compression has always worked on A740.

`ubwc_enabled` starts out as `true` (`tu_image.cc:470`), and three gates clear
it, of different nature:

| Gate | Condition | Nature |
|---|---|---|
| sparse | `flags & SPARSE_RESIDENCY` → `ubwc_possible()` false | **structural**, see below |
| mutable | list not all in one `fd6_ubwc_compat_type` and `ubwc_all_formats_compatible == false` | driver conservatism |
| linear | `force_linear_tile` → `tile_mode = TILE6_LINEAR` | layout decision |

The second gate is a bit flag for the whole device, but the check before it is
per-list: if all formats of the list give the same non-`UNKNOWN`
`fd6_ubwc_compat_type`, the block is skipped entirely and UBWC stays. On A740
`ubwc_unorm_snorm_int_compatible = True`, so `{R8G8B8A8_UNORM,
R8G8B8A8_UINT}` already works in stock. This is exactly what **0006** fixes for
the B8G8R8A8 family, and it is verified numerically: probe T gives `memreq`
`0x100000` on stock and **`0x102000`** with the patches, with roundtrip
0/262144 — i.e. the UBWC metadata appeared, not just the layout changed.

It is important not to conflate two different gates: `ubwc_all_formats_compatible`
(a device property) and `ubwc_possible()` (a check inside the driver, which does
not depend on the generation). Therefore on the 830 casting the format of a UBWC
image is allowed, while UBWC on sparse resources is disabled there too.

### Why sparse + UBWC can't just be turned on

Not because of the hardware, and not only because of fast-clear. UBWC metadata
lives in a separate plane of the same allocation:

```c
struct fdl_layout {
   struct fdl_slice slices[...];        /* data planes */
   struct fdl_slice ubwc_slices[...];   /* UBWC metadata plane */
   uint64_t layer_size;
   uint64_t ubwc_layer_size;
   uint32_t mip_tail_first_lod;         /* for sparse resources */
};
```

`vkGetImageSparseMemoryRequirements` computes the requirements **from the data
only**: `fdl_sparse_miptail_size/offset` work exclusively off `slices[]` and
`layer_size`, and there is not a word about `ubwc_slices[]` there. The
application is told "bind `[mipTailOffset, +mipTailSize)` with a stride of
`layer_size`" — and neither of these ranges covers the metadata. So the
metadata of the bound tile will stay whatever the allocator left it as, and the
decompressor will read garbage.

This cannot be worked around within the API: `vkQueueBindSparse` accepts only
those offsets that the driver itself reported, and the metadata cannot be hid
inside the miptail — the layout and sizes are determined by the format. The
second layer is a guaranteed zero on unbound tiles: fast-clear exists on A740,
and the unpacker on an unbound tile may return something other than zero. The
upstream stub "Enable UBWC if nonResidentStrict isn't enabled"
(`tu_image.cc:332`) is closed **intentionally**: `tu_device.cc:1323` hardcodes
`sparseResidencyNonResidentStrict = true`. The driver itself declares "I don't
guarantee reading unbound as 0" — and that is exactly what is incompatible
with UBWC. The coupling is not accidental.

The benefit of a workaround would only be perf (bandwidth on bound tiles), and
for D3D12 it is zero: UBWC is an internal driver optimization, D3D12 neither
requests nor observes it. The harm from unbound metadata outweighs the
economics.

But turning the flag on is a bet on the unverified. If the assumption is wrong,
UBWC images with changing formats will go through silently, without a create
error, and this is the non-sparse path — exactly the class where data corruption
was already observed on stock and where 0006 (P6) fixes the situation by
merging B8G8R8A8 on the INT type.

From there vkd3d reaches the needed threshold without edits. The
`TiledResourcesTier` gate consists of three steps: exactly one is removed (feature
exposure), the second passes fully on A740, the third runs into 3D sparsity,
which A740 doesn't have, and returns `TIER_2` — exactly what feature level 12_0
requires.

## 3. Fact 1 — 12_0 is reachable even without the force

Forcing `VKD3D_FEATURE_LEVEL=12_0` through `game-tweaks.json` raises the level
regardless of the driver, and **games launch and run**. So neither the
application, nor Proton, nor the rendering stack require anything beyond what
the driver provides — the force merely masks the gate. Hence: 12_0 can be
obtained honestly, by removing the gate.

The force remains a working crutch, but it doesn't cure the cause: the real
tiled resources are still not handed out, and the application gets TILED
capabilities that the driver doesn't provide.

## 4. Fact 2 — the gate can be lifted

The gate is removed, the missing support added, vkd3d patched. All three Mesa
patches are implemented, built and verified on the device — the stock driver
26.2.3 and one built from the patched sources, full logs in `results/`.

| Patch | What it does | Verification (stock → patches) |
|---|---|---|
| **0004** (P1+P3) | gate removal + rejection on create for the sparse+MUTABLE→linear hole | `sparseResidencyImage2D` `0 → 1`; hole creates `SUCCESS → FEATURE_NOT_PRESENT` |
| **0005** (P4) | uniform-swap lists work as tiled+sparse (implementation of the upstream TODO) | probe S: `FAIL` (all zeros) → `PASS`, both views exact, transfer 0/16384 |
| **0006** (P6) | B8G8R8A8 family merged on an INT-compatible type | probe T: `memreq 0x100000 → 0x102000` (UBWC metadata), roundtrip 0/262144 |

The key measurement is **S**: on stock the image is created but reads back all
zeros (the linear+sparse hole; create passes only because the feature and the
query lie). With 0004 create is honestly rejected, with exp-5 — it is created
and both views read exactly.

The key measurement for 0006 is **T**: the appearance of `0x2000` of UBWC
metadata at the same size, i.e. compression itself turned on, not just the
layout.

The boundary is checked by probe **S2**: mixed-swap `{BGRA8, RGBA8}` and the
NULL list remain rejected — on stock they passed. Separately rejected are E
(`{RGBA8, BGRA8}`) and F (NULL list) — see section 6 for the consequences for
vkd3d.

Separately verified that there is nothing to break: the D3D12 sampling paths
(float and real UINT) are clean, and the apparent 8888-INT read anomaly is a
combination of an UNORM image with a usampler, i.e. the specified "poison"
value — a test artifact, not a driver bug. Probe R (real `R8G8B8A8_UINT` +
usampler) reads exactly.

## 5. Nuance for upstream

In Mesa `main` the gate is present in two places:

- exposure of `sparseResidencyImage2D`;
- `vkGetPhysicalDeviceImageFormatProperties2` — the gate was added to the
  format property query path (commit "tu: Don't advertise sparse residency when
  the pdev can't do it", Emma Anholt, 03.09.2026).

vkd3d relies on exactly the second: `D3D12_FORMAT_SUPPORT2_TILED` is set from
the result of querying formats with `SPARSE_RESIDENCY`. Therefore the patch
must remove both gates — otherwise the feature will be advertised but TILED
won't.

In the version shipped in the Armada image (26.2.3) the second gate doesn't
exist yet: only the exposure is edited there. When moving to a fresher Mesa
there will be two edits.

## 6. Reproduction

`tests/rp6-vkd3d-sparse-test.c` — the test on which all the results above were
obtained. Key probes: **A** — sparse machinery working; **S** — the hole (on
stock the data is corrupted, on 0004 create is rejected, on 0005 both views are
exact); **S2** — the boundary (mixed-swap and the NULL list stay rejected);
**T** — P6; **N** — planar NV12 with and without MUTABLE; **V** — enumeration of
all 70 compatibility lists that vkd3d can possibly build; **G** and **M** — UBWC
controls (with metadata / without). The full matrix and verdict are in the file
header.

The class list for probe V is generated from the vkd3d sources rather than
enumerated by hand, and when vkd3d is updated it needs to be regenerated:

```sh
scripts/gen-vkd3d-lists.py /path/to/vkd3d-proton > tests/vkd3d-fmt-classes.h
```

Procedure:

```sh
# 1. toolchain (the device has no compiler, everything runs in podman)
podman run --name mesa localhost/fedora:44 \
    dnf install -y meson ninja-build gcc gcc-c++ python3-mako ...
podman commit mesa localhost/mesa-build-fedora44-full

# 2. Mesa 26.2.3 + patches 0001-0006
scripts/build-turnip.sh          # meson setup + ninja in the container
scripts/rebuild-turnip-msm.sh    # incrementally, if flags changed

# 3. test
scripts/build-test.sh
scripts/run-test.sh ""                          results/test-stock.log
scripts/run-test.sh .../freedreno_icd.json      results/test-patched.log
```

A run takes about a second, so there is no point in filtering probes — the full
log is more useful. Ready logs of both runs are in `results/`.

### Two mistakes the build went through

Both are reproducible, and both give an equally vague symptom —
`vkEnumeratePhysicalDevices` returns 0 devices and the test prints
`find turnip -> -3`.

1. **`-Dfreedreno-kmds=kgsl` (the Mesa default is `msm`).** The device has no
   `/sys/class/kgsl`, and the enumeration path for kgsl —
   `open("/dev/kgsl-3d0")` (`tu_knl.cc:343`) — silently returns
   `VK_ERROR_INCOMPATIBLE_DRIVER`. Signs: the `.so` contains the strings
   `kgsl_3d0`/`kgsl_bo_init`, while there is no `drmGetVersion` import.
2. **Where the result gets copied.** The container mounts `/out` at the root of
   `build/`, while the ICD manifest points to `build/out/`. If you copy into
   `/out/`, the loader picks up the old binary, and the result looks like "the
   patches didn't apply". You should check both copies with `ls -l` rather than
   trusting `EXIT=0`.

Diagnostics that came in handy: `TU_DEBUG=startup` (is there an instance),
`VK_LOADER_DEBUG=all` (did the loader find the ICD),
`objdump -p | grep NEEDED` and
`nm -D --undefined-only | grep drmGetVersion` (was the msm-KMD compiled in).

### Where 0004 turns into a rejection for vkd3d

The rejection is correct, but vkd3d has no degradation path: `resource.c:1207`
on `vkCreateImage` failure writes a WARN and returns the HRESULT outward, with
no retry and no fallback to another path. Therefore it matters which exact set
of combinations is actually unreachable.

Analysis of `vkd3d_get_format_compatibility_list` (`resource.c`):

- **regular formats** — there is always a list, the ready-made
  `device->format_compatibility_lists[Format]`, and for typeless-32-bit
  additionally `R32_{UINT,SINT,SFLOAT}` (D3D11 spec 5.3.9.5).
  `MUTABLE_FORMAT_BIT` is set only after
  `if (list->format_count < 2) return false;`, i.e. MUTABLE and the list go
  together: no list — no MUTABLE. No conflict.
- **the branch with a zeroed list** (`resource.c:826-827`, MUTABLE under
  `memset(compat_list, 0, ...)`) requires `DISABLE_UAV_COMPRESSION` /
  `DISABLE_COLOR_COMPRESSION` / `DISABLE_SIMULTANEOUS_UAV_COMPRESSION` —
  off by default, not encountered in a normal game.
- **planar formats** — the only default case where MUTABLE is set without a
  list:
  ```c
  if (format->vk_aspect_mask & VK_IMAGE_ASPECT_PLANE_0_BIT) {
      *vk_flags |= VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT;
      return false;
  }
  ```

This connects with `sparse_resource = !heap_properties` (`resource.c:732`): all
`CreateReservedResource` (`resource.c:4916` passes `NULL`) and any
`CreateCommittedResource` without a heap get
`SPARSE_BINDING|RESIDENCY|ALIASED`. The upshot: **NV12 on the default/reserved
heap** gives MUTABLE + SPARSE + no list, i.e. exactly the 0004 rejection.

### NV12: measured, the isolation works

Probe **N** (added to the test) — planar NV12 with sparse, with and without
MUTABLE:

| | stock | 0004–0006 |
|---|---|---|
| N1: NV12 + sparse, **without** MUTABLE | SUCCESS | **SUCCESS** |
| N2: NV12 + sparse, **with** MUTABLE | SUCCESS | `FEATURE_NOT_PRESENT` |
| N3: query NV12 + sparse, both variants | `FORMAT_NOT_SUPPORTED` | `FORMAT_NOT_SUPPORTED` |

N1 is exactly the prediction confirmed by measurement: without MUTABLE the
"NV12 is always linear" branch is not executed at all, `force_linear_tile` is
not set, `tile_mode` stays `TILE6_3`. That it is tiled rather than linear
follows from 0004 itself: it rejects any sparse image with
`tile_mode != TILE6_3`, so on the patched driver a successful sparse-create
cannot be linear.

N3 is not a regression: the same `FORMAT_NOT_SUPPORTED` exists on stock, the
cause is upstream (`tu_formats.cc:730-732`,
`/* Don't support multi-planar formats with sparse yet */`). Inside the driver
there is a discrepancy here — create allows, query rejects — but it doesn't
affect vkd3d: `utils.c:686` returns before the query for planar formats ("Planar
and depth-stencil formats do not support sparse in D3D12").

**Conclusion: MUTABLE and SPARSE are not obliged to combine, and the decoupling
is one line in vkd3d.** MUTABLE is functionally unnecessary for planar, as the
comment next to it admits: "Just be conservative here". The proposed edit:

```c
if (format->vk_aspect_mask & VK_IMAGE_ASPECT_PLANE_0_BIT) {
    if (!sparse_resource)                 /* NV12 + sparse is linear anyway */
        *vk_flags |= VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT;
    return false;
}
```

0004 should **not** be weakened for this: a NULL list means the application may
reinterpret into any compatible format, and safety cannot be proven for such a
list in principle — the helper says outright "presumably one would have the swap
set". The decoupling belongs on the vkd3d side.

### Enumeration of all lists vkd3d can possibly build

The question "will a list built by vkd3d itself ever reach the rejection" is
settled not by reasoning but by enumeration. Both tables that vkd3d uses for
this are static:

- `libs/vkd3d/utils.c: vkd3d_formats[]` — DXGI → VkFormat map;
- `libs/vkd3d/utils.c: dxgi_format_compatibility_list[]` — the lists
  (`image_format` + `view_formats`) from which
  `vkd3d_init_format_compatibility_lists` assembles
  `device->format_compatibility_lists[]`.

From these, `tests/vkd3d-fmt-classes.h` is generated
(`scripts/gen-vkd3d-lists.py`) — 72 classes whose list has ≥ 2 formats (two of
them were added later, §21.3). The counts in the sections below, made on the
first 70 classes, are left as they were.
This condition is exactly what decides everything: `MUTABLE_FORMAT_BIT` is set
only after `if (list->format_count < 2) return false;`, so **MUTABLE always
comes with a list**, and the "MUTABLE without a list" case on non-planar formats
is unreachable.

The enumeration zone is narrowed by two facts, both from the code:

- a sparse resource in vkd3d is exactly `CreateReservedResource`:
  `d3d12_resource_create_vk_resource` passes
  `heap_properties = resource->flags & VKD3D_RESOURCE_RESERVED ? NULL : &resource->heap_properties;`,
  so `sparse_resource = !heap_properties` is true only for reserved.
  Fallback goes to `d3d12_resource_create_committed`, i.e. **not** sparse.

Probe **V** asks the driver about each class three times — `SPARSE+MUTABLE`
with a list (exactly what vkd3d does for reserved), `MUTABLE` without sparse,
and `sparse` without mutable and without a list — under two usage sets: full
(`SAMPLED|STORAGE|TRANSFER`) and the one vkd3d itself uses to query sparse
support (`TRANSFER_SRC|TRANSFER_DST`, `utils.c`). For those that pass the query,
`vkCreateImage` is done: the query is only a necessary condition; the whole
point of 0004 is that query and create disagreed.

| usage | accepted | rejections by the `sparse × mutable` gate | query/create gaps | rejections not due to sparse |
|---|---|---|---|---|
| full | 49/70 | **0** | **0** | 21 |
| vkd3d's | 64/70 | **0** | **0** | 6 |

**No list that vkd3d is capable of building is rejected by the 0004 gate.** All
rejections are "sparse itself is forbidden" or "format+usage not supported at
all" (BC*, NV12/P010/P016, R32G32B32*), and these are long-standing upstream
limitations. The V block on stock and on the patches is **byte-identical**,
i.e. 0004–0006 changed not a single verdict for real vkd3d lists.

Two consequences:

1. The 0004 rejection is **unreachable** from the normal vkd3d path. The only
   path that lands in it is the planar branch without a list (probe N2), and
   there the fix is one line, described above.
2. The E/F hole that 0004 closes (silent linear+sparse) is **unreachable from
   vkd3d as well**: E is the list `{RGBA8, BGRA8}`, F is the NULL list, and
   vkd3d builds neither of them. So 0004 closes a real bug without changing
   behavior for any real consumer. This is the answer to the question that
   remained open: there is no risk to games, and there are no grounds for
   dropping 0004 either.

## 7. Swapping the driver on the device

Edits to files under `/usr` on the device are not made. An edited file in
`/usr` is a divergence from the installed package: it breaks install
verification and, after a build image update, resurfaces as system corruption —
and not at the moment of the edit, but later and in a different update, so the
link to the cause is lost.

So the driver is swapped by **temporary mounting**: the patched file lives
outside `/usr` (e.g. in `/var/lib/rp6-mesa/`), and a bind-mount is placed over
the packaged one. The mount point does not survive a reboot — after a reboot
the system is stock again, nothing needs to be cleaned up, and the update goes
through cleanly.

```sh
sudo mount --bind /var/lib/rp6-mesa/libvulkan_freedreno.so \
  /usr/lib64/libvulkan_freedreno.so   # after reboot the mount is gone
```

If the swap needs to persist within a session, the mount is set up as a unit in
`/etc/systemd/system` — `/usr` is again untouched in the process. For
vkd3d-proton, if a library swap is needed, the same trick applies: the file
lives outside the prefix and is mounted in place.

## 8. Running a game on the device and reading logs

The game is a third-party repack outside Steam, the prefix comes from Heroic
(sideload). Launch goes directly through proton, bypassing both Steam and
Heroic: Heroic adds umu-launcher, and then you can't see exactly what the game
gets.

```sh
scripts/run-game.sh stock 300     # packaged driver — baseline
scripts/run-game.sh patched 120   # patched one from build/out via VK_DRIVER_FILES
```

```sh
scripts/curate-game-log.sh results/raw/game-stock.log   # filter manually
```

### What matters in the script

- **CWD = the game folder.** RE4 opens `re_chunk_000.pak` and
  `local_config.ini` relative to the current directory. Launching from the repo
  left an empty `local_config.ini` (32 bytes) in the repo instead of the real
  one (20 KB), and the game showed a **black screen** — while the vkd3d
  rejections were exactly the same as in a successful run. So the black screen
  has nothing to do with the feature level.
- `VK_DRIVER_FILES` points to `build/out/freedreno_icd.json`; `/usr` is not
  touched. With it the device is a single one (Adreno), without it — two
  (Adreno + llvmpipe).
- `STEAM_COMPAT_APP_ID=2050650` is needed only by protonfixes (fix selection).
- Logs: the full proton/wine output — `results/raw/game-<mode>.log` (in
  `.gitignore`: 1.2 MB, of which only a handful of lines are significant), the
  filtered one — `results/game-<mode>.log` (collected by
  `scripts/curate-game-log.sh`).

### What to look for in the log

| Line | What it means |
|---|---|
| `d3d12_device_create: Feature level 0xc100 is not supported` | rejection on 12_1 (`0xc100`) |
| `d3d12_device_create: Feature level 0xc000 is not supported` | rejection on 12_0; after that vkd3d falls back to 11_1 |
| `dxgi_vk_swap_chain_init` / `Reallocating swapchain` | device created and a mode change is under way — the game started |
| `DxvkInstance::createInstance: Failed to create Vulkan instance` | DXVK failed to create a Vulkan instance: the game won't launch at all |
| `[Vulkan Loader]` | loader output (`VK_LOADER_DEBUG`); with `error,warn` it stays silent if the ICD loads without errors |

`VK_LOADER_DEBUG` defaults to `error,warn`; the full picture for the ICD comes
from `VK_LOADER_DEBUG=all scripts/run-game.sh patched`.

### What was measured

- **stock, CWD = the game folder**: rejections of 12_1 and 12_0, fallback to
  11_1, swapchain 1920x1080 → 2560x1440, postprocessing pipelines load, **the
  game loads**. The game has no separate "12_0 only" mode — 11_1 satisfies it
  (`results/game-stock.log`).
- **stock, CWD = the repo**: the same rejections, but `local_config.ini` went
  into the repo → black screen.
- **a single stock ICD** (via `VK_DRIVER_FILES` pointing at the packaged
  freedreno) — same behavior: so "one device instead of two" by itself changes
  nothing.
- **the patched ICD**: `sparseResidencyImage2D = true`, but DXVK dies on
  `vkCreateInstance` before the game even (`results/game-patched.log`). It is
  not a dependency problem: the patched `.so` links only against `libz`,
  `libdrm`, `libexpat`, `libstdc++` and `libc` — gallium is compiled into it.
  The cause of the rejection is analyzed in section 9.

## 9. Why the patched driver failed vkCreateInstance

The symptom looked enigmatic: `VK_LOADER_DEBUG=all` showed that the loader
finds and loads the ICD without a single error, yet the DXVK call doesn't even
produce the line `vkCreateInstance layer callstack setup to:` — i.e. the
rejection happens before entering the loader, inside winevulkan. Hence the
false conclusion "the patch is to blame".

The real cause is the build configuration, not the patch code:
`scripts/build-turnip.sh` built meson with `-Dplatforms=` (empty). Such a
build is fine for our own test from section 6 — it doesn't show a window — but
then the driver has no platform WSI extension at all. Comparing `vulkaninfo`:

| | `VK_KHR_xcb_surface` | `VK_KHR_xlib_surface` | `VK_KHR_wayland_surface` |
|---|---|---|---|
| image's packaged driver | yes | yes | yes |
| patched (`-Dplatforms=`) | no | no | no |

winevulkan translates the game's win32 surface (in DXVK's enabled list —
`VK_KHR_win32_surface`) into a host surface, i.e. xcb or wayland. With neither
present the translation is impossible, the instance is not created, and DXVK
dies.

The manifest shows exactly the same discrepancy: the built driver is a
different `libvulkan_freedreno.so`, not "the same one but with a patch", and it
must be compared with the stock one by its extension list, not by file name.

Fixes:

- `scripts/build-turnip.sh`: `-Dplatforms=x11,wayland` — the same set as in the
  image's packaged build (`image-build/mesa-x86-build.sh`);
- `container/Containerfile.mesa-build`: added the WSI dependencies
  (`libxcb-devel`, `libX11-devel`, `libxshmfence-devel`,
  `xcb-util-keysyms-devel`, `wayland-devel`, `wayland-protocols-devel`).
  Without them meson fails at the dependency check instead of silently building
  a driver without WSI;
- `container/Containerfile.mesa-glslang`: the package name in Fedora 44 is
  `glslang`, not `glslang-tools` (there is no `glslang-tools` in the repo, the
  install fails); the base was switched to the build image, otherwise it lacks
  meson/ninja/gcc;
- the ICD manifest is brought to the shape of the packaged driver:
  `api_version "1.4.341"`, `file_format_version "1.0.1"`,
  `library_arch "64"`.

Rule for the future: **do not run a driver build and a game at the same time.**
The device's memory (7.3 GB) is shared with the GPU; the game and
`ninja -j$(nproc)` together drive the system into OOM — the cursor freezes and a
reboot is needed. The same was observed last time, according to the user, and
the patch lost then was most likely caused by exactly this. `scripts/build-turnip.sh`
has a check for a running Wine before the build starts.

The image's own build script agrees: `image-build/mesa-x86-build.sh` (a copy of
the recipe) builds turnip with `-Dplatforms=x11,wayland`, i.e. the packaged
driver does have WSI and the discrepancy was only in the quick-start build
here. Also useful for the future from there: pressure-vessel runs a `dlopen`
inspection of every provider-ICD and silently drops the one that fails to
resolve some dependency — when swapping the driver through Steam this would
have looked like "ICD not found" rather than a loading error.

### Gate removed on a live application

After rebuilding with WSI (`results/game-patched.log`): DXVK creates the
instance, the device is created, and — the main thing — **12_0 is accepted**.
The rejection `Feature level 0xc000 is not supported` is gone, while the
rejection `0xc100` (12_1) remains, as it should: 12_1 runs into a different
gate (`ROVsSupported` + `ConservativeRasterizationTier >= 1`), and that is not
part of the task.

The proof is strict because the log trigger is tied to the code:
`WARN("Feature level %#x is not supported")` in `libs/vkd3d/device.c:10880`
sits exactly on the `!d3d12_device_supports_feature_level(...)` branch — i.e.
it is printed if and only if the requested level is above
`max_feature_level`. Comparison under identical conditions:

| | `d3d12_device_create` attempts | feature level rejections | max |
|---|---|---|---|
| packaged driver | 4 | `0xc100` (12_1) and `0xc000` (12_0) | 11_1 |
| patched | 3 | only `0xc100` (12_1) | **12_0** |

The attempt with `minimum_feature_level = 12_0` got no rejection, so
`max_feature_level >= 0xc000`. Meanwhile the game did not die: the process
lived to the end of the run (code 124 — our timeout), zero `err:`, zero
`Exception` in the log, and the log header says
`sparseResidencyImage2D | true`.

### Why 0xc100 (12_1) remains: one unimplemented extension (04.10.2026)

Above §9 called the 12_1 condition "`ROVsSupported` +
`ConservativeRasterizationTier >= 1`" and put it outside the task. Now it is
opened up fully — from the vkd3d-proton sources (`libs/vkd3d/device.c`, master
of 04.10.2026) and measurements on the device.

The `max_feature_level` cascade in `d3d12_device_init_caps()`:

    11_0 → 11_1 (OutputMergerLogicOp + storage slots)
        → 12_0 (TiledResourcesTier >= 2, ResourceBindingTier >= 2,
                TypedUAVLoadAdditionalFormats)
        → 12_1 (ROVsSupported && ConservativeRasterizationTier >= 1)
        → 12_2 (SM 6_5, RT 1_1, mesh, VRS tier 2, conservative tier 3, ...)

For us everything holds except `ROVsSupported`:

- **ConservativeRasterizationTier >= 1** — holds. Requires
  `EXT_conservative_rasterization`; Turnip enables it at `chip >= 7`
  (`tu_device.cc:312`), and both the packaged 26.2.3 and our build expose it
  (measured with `vulkaninfo` 04.10; `degenerateTrianglesRasterized = true`,
  i.e. the tier is no lower than 1, and by the function
  `d3d12_device_determine_conservative_rasterization_tier()` it is at least 2).
- **`ROVsSupported` = `fragmentShaderPixelInterlock && fragmentShaderSampleInterlock`**
  from `VK_EXT_fragment_shader_interlock` (`device.c:9270`). This is **not**
  rast-order-access: `VK_EXT/ARM_rasterization_order_attachment_access` exists
  in Turnip, but vkd3d doesn't read it for ROV.
- **`VK_EXT_fragment_shader_interlock` is not implemented in Turnip.** Checked
  against the ICD (`vulkaninfo` with `VK_DRIVER_FILES`, 04.10):
  `freedreno_icd` — 0; the line in the system output comes from `lvp_icd`
  (lavapipe). In the `src/freedreno` tree — not a trace of it, even though the
  SPIR-V/NIR infrastructure exists (`spirv_to_nir.c:7339`,
  `SpvOpBeginInvocationInterlockEXT`).

Status in the Mesa upstream (gitlab API, 04.10.2026): implemented in RADV
(MR 22250, via the HW primitive PoPS — `radv_has_pops`), ANV (enabled
unconditionally), lavapipe and zink; for nvk there is an open MR 38206. For
Turnip — neither an MR nor an issue.

Conclusion: 12_1 runs into exactly one extension not implemented in Turnip.
There is no publicly documented interlock primitive for Adreno, so an honest
implementation would be emulation: a spinlock table hashed by pixel in global
memory plus a fence (ir3 has both atomics in fragment shaders and the `fence`
instruction), with open questions: deadlock if a call is killed inside the
section, and helper-invocations. Enabling the flags without lowering would be
a lie about the caps. Also deserving a separate decision is that a single
consumer — `ROVsSupported` for the 12_1 flag — while RE4 doesn't use ROV at
all and runs on 12_0.

A force exists (`VKD3D_FEATURE_LEVEL=12_1`,
`d3d12_device_caps_override()`: sets `ROVsSupported = TRUE` and
`max_feature_level = 0xc100`), but this is caps spoofing; the check of the
"options" was done by measuring extensions and reading the vkd3d code, without
it.

### What remains unsolved

The game didn't reach a window: in 200 s `dxgi_vk_swap_chain_init` never
appeared (in the stock run it occurs right after the device is created), and on
the screen there was a window with the text "your GPU was not supported". This
is the game's own rejection, not proton's: the log is clean — zero `err:`,
zero `Exception`, the process lived to the end of the run (code 124 — our
timeout).

Compared to stock you can see the game went further than usual: it raises the
shader cache (`existing entries: 9962, new entries: 1898`) and reaches
`d3d12_device_CheckFeatureSupport`. So the 12_0 path works, while the
capability check breaks, and the game answers it with a rejection based on
requirements.

The guess was this: by design 0004 rejects create for the combination sparse +
MUTABLE without a compatible format list, while vkd3d sets
`MUTABLE_FORMAT_BIT` liberally and not always with a list; at 11_1 the game
doesn't touch this path, at 12_0 it does, and the 0004 rejection hits the
target. **The version was checked and disproved:** exp-3 removed
exactly that rejection while keeping the gate removal, and the game's behavior
didn't change — the swapchain still didn't come up (no
`dxgi_vk_swap_chain_init` line), the "your GPU was not supported" window still
appeared, the run still ended without a single `err:`. So the cause is not the
0004 rejection.

What needs looking at is the capability check the game itself performs: it is
called after device creation at 12_0 and concludes that the GPU is unsupported.
Tellingly, the user saw the same window last time as well, with a different
driver build — i.e. this is a stable symptom of 12_0, not a regression of the
current build. The candidate is the updated vkd3d, which at 12_0 reports a
different set of capabilities than the game expects; that is a separate branch
of the analysis.

The line `CheckFeatureSupport: Assuming device does not support tile based
rendering` is not a discriminator: it is present in the stock run too.

Separately about memory: the 7.3 GB of RAM on the device is shared with the
GPU. The game holds ~1.2 GB resident plus ~1.1 GB in zram, i.e. swapping is
happening; no OOM was observed meanwhile.

### Candidate No.1: the shader model (being checked)

The only line that is absent in the patched run and present four times in the
stock one is `d3d12_device_caps_init_shader_model: Enabling support for SM
6.6.` In the patched run it is absent in all three device creations. This
matters: levels 6.0/6.2/6.3/6.5 vkd3d prints via `TRACE` — with normal logging
they are not visible, while 6.6/6.7/6.8 go via `INFO`. The absence of the line
means `max_shader_model <= 6.5`.

The 6.6 condition (`device.c:9814`) rests entirely on physical device
properties: `computeDerivativeGroupLinear`, `shaderBufferInt64Atomics`,
`shaderInt8` and
`d3d12_device_supports_required_subgroup_size_for_stage(COMPUTE)`. Comparing
`vulkaninfo` for both ICDs shows that the Adreno property layout matches,
except for the sparse bits themselves:

| | stock | patched |
|---|---|---|
| extensions | 211 | 211 |
| `feature = true/false` lines | 416 | 416, one difference |
| differences | — | `sparseResidencyImage2D`, `sparseResidency{2,4,8}Samples` |
| `subgroupSize` / `minSubgroupSize` / `maxSubgroupSize` | 128 / 64 / 128 | same |
| `subgroupSupportedOperations` / `Stages` | 10 / 6 | same |
| `computeDerivativeGroupLinear`, `shaderInt8`, `shaderBufferInt64Atomics`, `subgroupSizeControl` | true | same |

So per the driver's data the 6.6 gate must pass in both cases. Either the
11:48 run log was taken from a different `build/out` build, or vkd3d reads
something beyond what `vulkaninfo` shows. This is resolved by a run with
`VKD3D_DEBUG=trace`: then vkd3d prints both `Enabling support for SM 6.5.` and
the version for 6.6.

The hypothesis agrees with the other facts: at 11_1 (the stock run) that same
rejection is absent because the game doesn't ask for 12_0. It is checked with
no code edits at all: `VKD3D_SHADER_MODEL=6_6`
(`d3d12_device_caps_shader_model_override`, `device.c:9646`) forces vkd3d to
report 6.6. If with this the window disappears and the swapchain comes up — the
cause is confirmed.

A side finding from the same comparison: our ICD manifest declared
`api_version 1.4.341`, while the packaged one is `1.4.354`, and that is exactly
the version the driver itself reports. It doesn't affect behavior, but the
mismatch was removed (`scripts/build-turnip.sh`).

## 10. The cause of the rejection found: not driconf, but `prefix=/out/usr`

### 10.1. The run with the force confirmed the hypothesis

`VKD3D_SHADER_MODEL=6_6 scripts/run-game.sh patched 200` — **the game ran**:
the swapchain is created and recreated (`1920x1080` → `1280x800`), no window
rejection. Log — `results/game-patched-sm66.log`.

The same run verified that the env reaches vkd3d: it prints
`d3d12_device_caps_shader_model_override: Overriding supported shader model: 6_6.`
So the non-working `VKD3D_DEBUG=trace` is a separate problem (not this run),
and environment variables reach vkd3d fine.

Separately: `VKD3D_FEATURE_LEVEL=12_0` on stock
(`results/game-stock-flforce.log`) also gives a working game. This reproduces
"fact 1" from the document in the `rp6-vkd3d-analysis` branch already on RE4.
The level force raises `max_shader_model` only to 6_0
(`d3d12_device_caps_override`, `device.c:10888`), so the SM level in this run
stays driver-side.

### 10.2. Why `vulkaninfo` didn't show the discrepancy

The 6.2 gate in vkd3d (`device.c:9750-9765`):

```c
denorm_behavior = properties.denormBehaviorIndependence != VK_SHADER_FLOAT_CONTROLS_INDEPENDENCE_NONE;
if (denorm_behavior && properties.driverID != VK_DRIVER_ID_NVIDIA_PROPRIETARY)
    denorm_behavior = properties.shaderDenormFlushToZeroFloat32 &&
                      properties.shaderDenormPreserveFloat32;
```

On Adreno `FTZ32 = true`, `Preserve32 = false` — i.e. SM 6.0. But `Preserve32`
comes not from the hardware but from a driconf option (`tu_device.cc:1033`):

```c
/* FP32 denorm preserve has to be emulated via soft-float. ...
 * ... but vkd3d-proton cannot emulate it itself so we have to allow it
 * to use our emulation. */
p->shaderDenormPreserveFloat32 = pdevice->instance->drirc.misc.enable_softfloat32;
```

and this option is enabled **only for the vkd3d engine** —
`00-turnip-defaults.conf:42`:

```xml
<engine engine_name_match="vkd3d">
    <option name="tu_enable_softfloat32" value="true" />
</engine>
```
`engine_name_match` is a regex (`xmlconfig.c:848`), compared with the engine
name from `VkApplicationInfo`. `vulkaninfo`'s engine is not vkd3d, so **both**
drivers show `Preserve32 = false`, and the property comparison saw no
difference. This is exactly why the "SM" candidate looked disproved.

### 10.3. Our driver doesn't read driconf at all

`xmlconfig.c:1360-1380`:

```c
/* parse from either $DRIRC_CONFIGDIR or $datadir/drirc.d */
if ((configdir = os_get_option("DRIRC_CONFIGDIR"))) { ... }
else {
    parseConfigDir(&userData, DATADIR "/drirc.d");
    parseOneConfigFile(&userData, SYSCONFDIR "/drirc");
}
```

`DATADIR` comes from the `prefix`/`datadir` of our meson configuration, and it
is this: `prefix = /out/usr`, `datadir = share` → `DATADIR = /out/usr/share`.
The directory `/out/usr/share/drirc.d` does not exist, `SYSCONFDIR` for us is
also not about `/etc`, and `DRIRC_CONFIGDIR` was never set in the runs. The
upshot: **not a single turnip driconf option was read by our driver**, including
`tu_enable_softfloat32`. The packaged driver reads
`/usr/share/drirc.d/00-turnip-defaults.conf` — hence it has SM 6.6 and ours
doesn't.

Measurement (patched + a swapped-in conf, engine matching everything):

```
$ DRIRC_CONFIGDIR=/tmp/opencode/drirc-test vulkaninfo   # patched
    shaderDenormPreserveFloat32 = true      # was false
```

A clarification on the check: the `engine_name_match` pattern must be empty
(`""`) rather than `"."` — `vulkaninfo` has an empty `engineName`, and `.`
requires one character and doesn't match.

### 10.4. Where the path comes from and why it diverged

The path is not computed, it is **baked into the binary at build time**:
`src/util/meson.build:421` emits `-DDATADIR=join_paths(prefix, datadir)`, and
the only use of this macro in the whole tree is `xmlconfig.c:1375`. It can be
checked directly in the library:

```
$ grep -a -o '/usr/share/drirc\.d' /usr/lib64/libvulkan_freedreno.so
/usr/share/drirc.d
$ grep -a -o '/out/usr/share/drirc\.d' build/out/libvulkan_freedreno.so
/out/usr/share/drirc.d
```

The image's packaged build goes through `rpmbuild`
(`image-build/build.sh:70`), and in the Fedora spec `prefix=/usr` — hence
`/usr/share/drirc.d`. Our build was installed with `--prefix=/out/usr`: that is
the container's staging directory, not a real install prefix, and on the device
`/out` doesn't exist at all.

So this is neither a driver bug nor the patches' fault: a mismatch between the
build prefix and where the package is actually installed.

### 10.5. What to fix

The game's rejection is not in patches 0004–0006 and not in vkd3d. The window
appeared because of empty driconf. Three ways, all closing the same cause:

| way | where | state |
|---|---|---|
| `--prefix=/usr` in `scripts/build-turnip.sh` | build | **working, verified** |
| `DRIRC_CONFIGDIR=/usr/share/drirc.d` | `run-game.sh` | was there, **removed** (masked the cause) |
| `~/.drirc` with the same block | home directory | was there, **removed** (masked the cause) |

The correct way was chosen — `--prefix=/usr`: it removes the cause rather than
the consequence, and it works for any launch, including a normal launch of the
game from Heroic, where neither `DRIRC_CONFIGDIR` nor our script is set.

The other two ways were **removed**, not kept as a fallback: both would mask the
cause. As long as `DRIRC_CONFIGDIR` was set in `run-game.sh` and `~/.drirc`
sat in the home directory, SM 6.6 could come up from either of them, and the
next debugging session would look for the cause in the wrong place.
`run-game.sh` now does `unset DRIRC_CONFIGDIR` instead of `export` — not
"doing nothing", but so that a stray variable in the caller's environment
doesn't substitute driconf and make the run look successful for the wrong
reason. The log header keeps the line `DRIRC_CONFIGDIR=<unset>`: that is what
proves the variable was absent.

Verified by rebuilding and running: the path in the binary became
`/usr/share/drirc.d`, the patches are in place
(`sparseResidencyImage2D = true`), the game runs **without** `DRIRC_CONFIGDIR`
and **without** `~/.drirc` — SM 6.6 comes up three times, the force is applied
zero times (`results/game-patched-prefixusr.log`, and again
`results/game-patched-clean.log` after both crutches were removed).

The `--prefix=/usr` edit is safe because `DATADIR` is not used anywhere else in
the tree — only the path to driconf changes. `SYSCONFDIR` (same trick,
`meson.build:418`) will yield `/etc/drirc`, which also doesn't exist, but that
is `parseOneConfigFile` on an optional file, not `parseConfigDir`.

`~/.drirc` duplicates the `<engine engine_name_match="vkd3d">` block from
`/usr/share/drirc.d` and is read after the system directories, so it doesn't
affect the packaged driver. Its `vkd3d` condition is deliberately not empty:
softfloat32 is emulated and costs performance, and Mesa explicitly advises
against enabling it for everyone — so ordinary DX11 games through DXVK are not
affected.

### 10.6. How to verify

```bash
# 1. path in the library (after rebuilding)
grep -a -o '/usr/share/drirc\.d' /var/home/armada/opencode/build/out/libvulkan_freedreno.so

# 2. the option is picked up without DRIRC_CONFIGDIR
env -u DRIRC_CONFIGDIR VK_DRIVER_FILES=.../build/out/freedreno_icd.json vulkaninfo \
  | grep shaderDenormPreserveFloat32        # expect true

# 3. the game comes up without an SM force
scripts/run-game.sh patched 200
grep "Enabling support for SM 6.6" results/raw/game-patched.log
```

A caveat about step 2: `vulkaninfo` doesn't report the engine name, so the
engine block isn't applied with it and `Preserve32 = false` — that's normal,
the same as with the packaged driver. You should evaluate with a game run, not
`vulkaninfo`.

## 11. Performance: what to measure with

MangoHud is a Vulkan layer, not DXVK, which is why it is needed for DX12 games:
`DXVK_HUD` is useless here; RE4 goes through `d3d12` = vkd3d-proton, DXVK does
not take part in this path. The layer is enabled implicitly and off by default,
activated by `MANGOHUD=1` (verified with `VK_LOADER_DEBUG=all`:
`Insert instance layer VK_LAYER_MANGOHUD_overlay_aarch64`).

```bash
HUD=1    scripts/run-game.sh patched 180   # overlay on screen
HUDLOG=90 scripts/run-game.sh patched 120  # overlay + CSV in /tmp/opencode/mangologs
```

`HUDLOG` is needed because FPS can't be read off the screen, while comparing
runs requires numbers. MangoHud writes `<app>_<date>.csv` (one sample per
second) and next to it `_summary.csv` — you need the first one; the second has
no data rows.

CSV format: the first row is device info, the second is the headers, data from
the third. On Adreno `gpu_core_clock` is filled in, `gpu_temp` exists, but
`cpu`/`gpu` (time in ms) does not — so CPU-bound and GPU-bound cannot be split
by time, only by `gpu_load`.

### 11.1. What RE4 showed

Two phases, unambiguously distinguishable (`results/game-patched-hud.log`):

| phase | fps | gpu_load | core clock |
|---|---|---|---|
| menus, loading | 55–70 | 3–37% | 475 MHz |
| gameplay | 12–19 | 85–99% | 680 MHz |
Temperature 68–71 °C, no throttling. So 13 fps in the game is a **GPU
bandwidth limit**, not vsync (frametime is not a multiple of 16.7 ms) and not
CPU (`cpu_load` 66% across eight cores, a significant part of which is wine).
GPU load rises as fps falls, and the core clocks up to the limit.

Also visible is a frequency drop to 220 MHz in the waiting phase
(`gpu_load` 0–11% at 19 fps) — the governor drops the overclock when the GPU is
idle. This is not the cause of the 13 fps.

What the measurement does **not** separate: how much of those 13 fps
`softfloat32` eats. The option is enabled by the driconf block for the `vkd3d`
engine and doesn't depend on the feature level, so it is present in both runs
below, and its contribution is inside both numbers.

### 11.1a. 12_0 versus 11_1 — is there a difference

`VKD3D_FEATURE_LEVEL=11_1` (the value format is `11_1`, parsing in
`d3d12_device_caps_override`, `device.c:9905`), same scene, same driver:

| metric (tail 40 s) | 12_0 | 11_1 |
|---|---|---|
| fps | 19.6 | 18.4 |
| gpu_load | 80% | 82% |
| cpu_load | 66% | 56% |
| core | 680 MHz | 680 MHz |
| gpu_temp | 68 °C | 66 °C |
| ram_used | 6.86 GB | 6.87 GB |
| swap_used | 3.15 GB | 3.13 GB |

**12_0 costs nothing** — neither in FPS nor in GPU load. The `cpu_load`
difference is within the scene's noise, and the windows are not fully
comparable (in the 12_0 run the core dropped to 220 MHz, i.e. part of the tail
was not gameplay).

Logs: `results/game-patched-fl1111.log`, `results/fps-patched-fl1111.csv`.

A side observation from the log: with the force to 11_1 the game asked for 12_0
and got the rejection `0xc000` — and went ahead and played on 11_1 anyway. So
the "your GPU was not supported" window doesn't latch onto the 12_0 rejection
but onto a separate SM 6.6 check. This independently confirms the conclusion of
section 10.

Also visible is the device state: RAM is 99% used (7.2 of 7.32 GiB) plus 3.1 GB
in zram. Swap is configured as zram, i.e. compressed pages sit in the same RAM.
Of the three explanations for why 13 fps, "ran out of memory" looks stronger
than "12_0 is expensive" — the latter has already been ruled out.

### 11.2. The DX11 attempt — didn't work, and here is why

RE4's `re4.exe` has a property name `via.render.RenderDeviceAPI` with values
`DirectX11 | DirectX12 | OpenGL | OpenGLES | Vulkan`, so the question "can it
run on DX11" is legitimate.

Checked by changing `[Render] Capability` in `local_config.ini`
(`DirectX12` → `DirectX11`): **the game doesn't read this value.** It rewrote
the key back to `DirectX12` on the next launch, and the log showed the usual
DX12 path — `d3d12_device_create`, SM 6.6, swapchain through vkd3d. So
`Capability` is an output key (which was determined), not an input one (what to
run with).

A non-working switching method does not yield the conclusion "DX11 is
unreachable": the command-line key has not been tried. The check should be done
like this — see whether the vkd3d markers (`d3d12_device_create`) appear in the
log when launched with an argument: if they are gone, the game went through
DXVK.

A caveat about the point of such a check: the DX11 path goes through DXVK, and
it doesn't verify what the patches were made for at the vkd3d level — neither
`12_0` nor SM 6.6 is required there. The driver itself, meanwhile, is not left
out: DXVK talks to Vulkan, i.e. to the same turnip, and its patches (UBWC lists,
clear-path, `TU_FORCE_PROPS`) do take effect in a DX11 game. So such a check
speaks about the health of the driver but says nothing about the patches of the
vkd3d stack.

About installed games: **God of War (AppId 1593500) is not DX12**, it has only
DX11 and Vulkan (this was previously recorded here by mistake). The DX12 games
among the installed ones are Avowed and Starfield. Launching them with the
patched driver is possible only with `VK_DRIVER_FILES`, as in
`scripts/run-game.sh`: Heroic's configs don't have this variable, so a launch
from the launcher goes on the stock driver `/usr`.

## 12. Defect of run-game.sh: the game outlives the timeout

`timeout --signal=INT` kills proton but not the game: `re4.exe` and
`wineserver` stay alive. The next run starts on top of them, in the same
wineserver, and the measurements come out mixed. Discovered on the DX11 attempt,
where a live `re4.exe` was present before launch.

The script now checks `pgrep -f 're4\.exe'` after the timeout, reports it on
stdout and kills the processes (first `SIGTERM`, then `SIGTERM` to wineserver,
then `SIGKILL` for the leftovers).

The number of `d3d12_device_create` lines in the log is deceptive: seven lines
are **one** device plus six warnings from `d3d12_device_create_vkd3d_queues`
about failed OOB queues for families 0 and 5, not seven devices.

## 13. Performance: FPS doesn't depend on anything in the driver

The task was to raise FPS in RE4 by editing the driver. It didn't work out, and
this is a positive result: **stock Mesa gives the same 13 fps** as the patched
one.

### 13.1 What was checked

The same segment, nine configurations. The metric is `p25` of the scene's frame
fps; loading and menu frames (50–85 fps) are excluded by the `fps < 30`
threshold, otherwise they inflate the number (in the "blur off" run the window
caught the loading and produced a nonexistent 17 fps).

| config | fps p25 | RAM peak | swap peak |
|---|---|---|---|
| stock (user's measurement) | 13.0 | — | — |
| patch, 12_0 base | 13.9 | 7.23 | 5.15 |
| patch, 11_1 force | 13.7 | 7.27 | 5.36 |
| softfloat32 off | 13.8 | 7.30 | 5.53 |
| `prefer_gmem` allocation | 12.9 | 7.27 | 5.55 |
| resolution 960x540 | 13.7 | 7.30 | 5.54 |
| shadows off | 10.8 | 7.31 | 6.52 |
| blur off | 2.2 | 7.23 | 4.33 |

RAM is in gigabytes, swap is in gigabytes. Total RAM in the system is 7.32 GiB.

### 13.2 Conclusions per hypothesis

- **Software FP32 emulation costs nothing.** `tu_nir_lower_softfloat32`
  rewrites every FP instruction into a call from `float32.glsl`, but with
  `enable_softfloat32=false` FPS doesn't change. So the game is not bound by
  arithmetic, and the 12_0 requirement doesn't cost performance.
- **Pixels don't matter.** 960x540 is 0.49x of the area, FPS +1. `frametime`
  meanwhile got worse (51 → 64 ms). `gpu_load` rose from 80% to 88%, i.e. on
  this GPU the load percentage is not indicative.
- **Feature level 11_1 doesn't help** (13.7 versus 13.9).
- **Memory allocation doesn't help**: `prefer_gmem` is no faster than
  `prefer_sysmem`.
- **Shadows and blur are not the cause**: disabling them worsens the result.

### 13.3 What is actually loaded

- CPU: 1.65 cores out of 8 in total, maximum per thread 29%. No single thread
  is loaded — the threads are idle, yet a frame is still 75 ms.
- GPU: `simple_ondemand`, `cur_freq` up to 680 MHz — the `max_freq` ceiling;
  the governor is ruled out. The governor can't be verified: writing to
  `devfreq` requires sudo.
- Memory: RAM 7.23–7.31 GB out of 7.32 GiB, i.e. 100% in every run. Swap grows
  from 3.1 up to 6.5 GB over a run, `so` up to 155 MB/2s, but `si` only
  2.6 MB/s and `wa` median 4% — no throttling in the face.

Summary: neither CPU, nor GPU, nor memory is loaded, yet a frame is 75 ms.
Stock Mesa is also 13 fps, so the limit is the hardware and the amount of
memory, not a turnip defect. FPS here cannot be raised by the driver.

### 13.4 A method error that had to be fixed

`ps -eLo` gives average CPU since process start, not instantaneous, and a
process's `pcpu` can exceed 100%. For an instantaneous per-thread picture,
deltas of `/proc/<pid>/task/*/stat` over a fixed window are computed.

## 14. The hypothesis "the driver doesn't use the capabilities of the 750/830" — checked and rejected

### 14.1 Where the hypothesis came from

In the device table `common/freedreno_devices.py` our a740 gets
`[a7xx_base, a7xx_gen2]`, while a750 gets `[a7xx_base, a7xx_gen3]`. The flags
that only gen3 has look like unused capabilities:

| flag | meaning | effect on a740 |
|---|---|---|
| `load_shader_consts_via_preamble` | constants via preamble | **corruption, see 14.3** |
| `load_inline_uniforms_via_preamble_ldgk` | inline uniforms via preamble | test can't distinguish |
| `has_generic_clear` | one clear for sysmem+gmem | test can't distinguish |
| `has_gmem_vpc_attr_buf` | VPC in GMEM | test can't distinguish |
| `has_compliant_dp4acc` | signed SDOT | test can't distinguish |
| `has_persistent_counter` | counters survive suspend | test can't distinguish |
| `has_abs_bin_mask` | `CP_SET_BIN_DATA5::ABS_MASK` | test can't distinguish |
| `ubwc_all_formats_compatible` | UBWC for all formats | **brings back the stock hole** |
| `max_draw_states = 64` | state slots | one place in the code, no effect on FPS |

Important: this is not a division by generation but observations from the
Qualcomm blob for a specific chip. Formally the 830 doesn't take part in the
table.

### 14.2 How it was checked

`TU_FORCE_PROPS` was added by editing `tu_device.cc` — a comma-separated list
of flag names — plus `TU_MAX_DRAW_STATES`. This allows checking the hypothesis
with one rebuild instead of a rebuild per flag. Each applied value is printed
to the log; there is no silent ignoring. Patch `0007`.

The correctness check is `rp6-vkd3d-sparse-test` (`scripts/run-test.sh`), the
game is not launched. One caveat: the test doesn't render a scene, so it cannot
possibly refute the render-path flags (bin mask, generic clear, VPC). "No
difference" means "not covered by the test", not "safe".

Incidentally `scripts/run-test.sh` was fixed: it ignored `RP6_TEST_BIN` from
`env.sh` and used a hard-coded `./rp6-vkd3d-sparse-test`, i.e. it only ran from
`build/` and didn't run from the repository.

### 14.3 Result

**`load_shader_consts_via_preamble` does not work on a740.** The test catches
real corruption, not cosmetics:

- `T probe: FAIL` — `UINT view read=MISMATCH` (EXACT in the baseline)
- `S probe: FAIL` — `s-uint=8 mismatches`
- view-cast probe: reads `0x00000000` instead of `0x37373737`
- `t-uint y7 x128: read 0x800787a5 want 0x80a58707`

The hypothesis was rejected by measurement, not by reasoning.

**`ubwc_all_formats_compatible` must not be enabled.** With it `vkCreateImage`
(MUTABLE+SPARSE) starts returning `SUCCESS` — i.e. the driver is **lying**
again, and vkd3d gets a linear sparse instead of a tiled one. Exactly the hole
that patches 0004 and 0005 close: the baseline build honestly returns
`FORMAT_NOT_SUPPORTED` / `FEATURE_NOT_PRESENT`. The flag doesn't "enable a
capability", it brings back wrong behavior.

**The test can't distinguish the other six flags.** None showed either
corrupted data or changes. This is not proof of safety — it is simply outside
the test's coverage.

### 14.4 Can `ubwc_all_formats_compatible` be fixed by another flag

Hypothesis: since the corruption from `ubwc_all_formats_compatible` is "we read
what we didn't write", maybe a second flag (say about UBWC coherency) will
remove it.

**In the code — there is nothing to use.** In the whole path of creating a
mutable UBWC image exactly two properties are read:
`ubwc_all_formats_compatible` (`tu_image.cc:541`, `tu_formats.cc:248`) and
`ubwc_unorm_snorm_int_compatible` (`tu_image.cc:350`, `freedreno_ubwc.h`). The
second is **already enabled** on gen2. The other UBWC gates
(`has_8bpp_ubwc`, `supports_uav_ubwc`, `has_ubwc_linear_mipmap_fallback`,
`supports_linear_mipmap_threshold_in_blocks`) are enabled for us,
`broken_ds_ubwc_quirk` is not about color formats. What comes next is not a
property but a bit in the descriptor: `fd6_view.cc:265` sets
`A6XX_TEX_MEMOBJ_1_MUTABLEEN`, and `fd6_format_table.c:396` meanwhile changes
the swap (`if (tile_mode && !is_mutable) return WZYX;`). No prop affects this
calculation — the hardware decides.

The only candidate that even sounds like "we read the wrong thing" is
`ubwc_coherency_quirk` (gen3, `False` for us; at a barrier with a shader stage
it adds `CACHE_CLEAN`, `tu_cmd_buffer.cc:10108`).

**Check.** `ubwc_coherency_quirk` was added to the `TU_FORCE_PROPS` list
(patch 0007), one incremental rebuild, then four runs of
`scripts/run-test.sh` with no rebuilds:

| config | `TU_FORCE_PROPS` |
|---|---|
| base | — |
| `ubwc_coherency_quirk` | `ubwc_coherency_quirk` |
| `ubwc_all_formats_compatible` | `ubwc_all_formats_compatible` |
| combo | `ubwc_all_formats_compatible,ubwc_coherency_quirk` |

Logs: `results/test-props-base.log`,
`results/test-props-ubwc_coherency_quirk.log`,
`results/test-props-ubwc_all_formats_compatible.log`,
`results/test-props-ubwc_all+coherency.log`.

| | base | +coherency | +ubwc_all | +both |
|---|---|---|---|---|
| `H memreq` | `0x100000` | `0x100000` | `0x102000` | `0x102000` |
| `H d1'` (R32_UINT view) | no UBWC → not engaged | same | 8 rows of garbage | 8 rows of garbage |
| `S2 vkCreateImage(MUTABLE+SPARSE)` | `FEATURE_NOT_PRESENT` | `FEATURE_NOT_PRESENT` | `SUCCESS` (hole) | `SUCCESS` (hole) |
| `T-tr` / `S-tr` | 0/262144, 0/16384 | 0/262144, 0/16384 | 0/262144, 0/16384 | 0/262144, 0/16384 |

**Result: the logs are pairwise byte-for-byte identical** (a diff without the
`TU_FORCE_PROPS` lines yields nothing for both base/`+coherency` and
`ubwc_all`/combo). Plus both fresh runs matched the already committed ones —
the rebuild shifted nothing and the runs are deterministic.

The check was meanwhile working rather than silently not firing: the test's
barriers contain `VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT` in srcStage in two
places (`rp6-vkd3d-sparse-test.c:380` and `:1426`), i.e. the quirk condition is
met and `CACHE_CLEAN` really is emitted.

**Conclusion: `ubwc_coherency_quirk` does not fix
`ubwc_all_formats_compatible`, and there is no other flag in this path.** Both
corruptions (the hole on MUTABLE+SPARSE and the garbage in `H d1'`) remain
exactly the same. There is nothing to pick as a set of props that would enable
on gen2 what gen3 enables — next, the workaround paths themselves were checked,
section 14.5.

### 14.5 Hardware hacks: `swap` can be cured, UBWC reinterpretation — cannot

"Hardware behavior" is not a sentence: the driver is full of hacks for
hardware, and half of them are about `swap` exactly. What is already used in
this path:

| Hack | Where | What it does |
|---|---|---|
| force `WZYX` for tiled | `fd6_format_table.c:396` | CCU ignores swap when tiling, TP respects it → keep both on `WZYX` |
| turn off UBWC/tile for mutable lists with a foreign swap | `tu_image.cc:559-578` | this is exactly the baseline behavior — works |
| staging blit instead of HW reinterpretation | `tu_clear_blit.cc:3096` | doesn't let HW do the wrong swap |
| S8→A8 to avoid using swap | `tu_clear_blit.cc:382` | "swap sometimes doesn't work with D24S8" |
| `if (!is_mutable) set_swap(WZYX)` in the GMEM patch | `tu_cmd_buffer.cc:2874` | same, but for attachments |
| separate swap recomputation for `store_cp_blit` | `tu_clear_blit.cc:5547` | "GMEM ignores swap, **except MUTABLEEN**" |

The hacks here are of two types: **(a)** disable the broken thing — there is an
abundance of those and all of them are already applied; **(b)** make HW compute
in a way it can't — there is none of that anywhere, nor can there be. One
unverified hack of type (a) remained.

#### 14.5.1 Hack 0004: `WZYX` also at `is_mutable`

From exp0002: at `is_mutable=true` the driver substitutes the view's real swap
(`fd6_format_table.c:491/514/543`) and sets `MUTABLEEN`; `T-tr` breaks
(0 → 261120/262144). So on a740 `MUTABLEEN` possibly enables reinterpretation
of UBWC metadata, but does **not** enable swap respect in the CCU, and write
and read diverge. This is cured by the same hack: remove `&& !is_mutable`.

A/B on a single test binary, 0002 is applied in both cases:

| | 0002 (control) | 0002 + 0004 |
|---|---|---|
| `T-tr` | **261120/262144** | **0/262144** |
| `S-tr` | **16384/16384** | **0/16384** |

Logs: `results/test-expD-control.log`, `results/test-expD.log`, patch
`experiments/exp-4-force-wzyx-when-mutable.patch`.

**All the corruption from `MUTABLEEN` turned out to be in `swap`, and the old
hack removes it.** Incidentally it turned out that 0004 alone (without 0002)
gives zero difference: under the `ubwc_all_formats_compatible` flag the list T
{BGRA8, BGRA8_UINT} passes the homogeneity check and never reaches the
`is_mutable` branch.

#### 14.5.2 Hack 0005: UBWC on, `MUTABLEEN=0`

The second question is whether 0004 cures the format reinterpretation itself.
It doesn't, because `H` doesn't change with 0004 anyway: for {RGBA8, R32_UINT}
the swap of both formats is `WZYX`, the hack touches nothing there. A separate
question is needed: **is the bit harmful, or is the capability missing?**

exp-5 keeps UBWC enabled for an incompatible list but doesn't raise
`is_mutable` — i.e. `MUTABLEEN=0` while UBWC stays:

| | base (no UBWC) | 0001 (UBWC + `MUTABLEEN=1`) | 0005 (UBWC + `MUTABLEEN=0`) |
|---|---|---|---|
| `H memreq` | `0x100000` | `0x102000` | `0x102000` |
| `H d1'` | not engaged | garbage `0x079d685e…` | **the same garbage, byte-for-byte** |

Log: `results/test-expE.log`, patch
`experiments/exp-5-ubwc-on-without-mutableen.patch`.

**The answer: the capability is missing, not "the bit is harmful".** a740 can't
read a UBWC-compressed image through a view of another format at all — with the
bit and without the bit you get the same garbage array. The hack doesn't cure
this: you can replace SWAP with SWIZ (`a6xx_descriptors.xml:53-56`, 2 bits per
component, TP respects it), but SWIZ permutes channels rather than changing the
format of UBWC metadata, and for attachments there is no SWIZ at all
(`RB_MRT_BUF_INFO` only gives `COLOR_SWAP`).

#### 14.5.3 What this changes

1. **The corruption from `ubwc_all_formats_compatible` consists of two
   independent parts.** A fresh `diff` of base ↔ flag: the unique damage is only
   `H d1'` and the holes on `S2`/`G0`/`E`/`F`. The other `FAIL`s (D/G/K/M/Q,
   "UNORM + usampler") are present in base too — the flag didn't add them.
2. **The `swap` part is cured by hack 0004**, but it cures exactly what it
   itself produces: without 0004 `is_mutable` breaks `T-tr`/`S-tr`, with 0004
   they return to normal. The test showed no case where 0004 did better than
   base.
3. **The `UBWC` part is cured by nothing** — that is exactly the missing a750
   capability on a740.
4. **Summary: enabling `ubwc_all_formats_compatible` on gen2 is still not
   allowed.** Hack 0004 makes the flag harmless (removes the swap corruption)
   but doesn't make it useful: there is no gain from UBWC on such lists,
   because reading through a foreign format is impossible anyway, and the holes
   on sparse-create remain.

A caveat about the tables: the line `H: memreq ... -> MUTABLEEN in play` is the
test's own inference from the memreq value, not a fact about the bit. In
exp-5 `MUTABLEEN=0` yet the line is the same; the fact was checked by
the `tu_image.cc` edit not raising `is_mutable`.

## 15. Measuring the props: vkmark and RE4

Flags are overridden via `TU_FORCE_PROPS` (patch 0007), i.e. one rebuild for
all configurations and no compiler drift between runs.

### 15.1 Method

```bash
$VK --winsys xcb --winsys-dir $D/lib64/vkmark --data-dir $D/share/vkmark \
    --size 1280x800 --present immediate \
    -b clear -b vertex -b shading -b texture
```

The metric is `FrameTime` per frame in ms (lower = better). Configurations are
alternated in an interleaved fashion (base/flag/base/flag…) rather than in
blocks, so that frequency and temperature drift doesn't line up with a single
config. **Comparison is only valid within one series**: between series the
patch set in the tree changed, and the vertex baseline moved from 0.210 (29.09)
to 0.141 (30.09).

Files: `results/vkmark-flags.txt` (series of 29.09, `has_gmem_vpc_attr_buf` and
the first estimate of `has_generic_clear`), `results/vkmark-ab-combo.txt`
(combo), `results/vkmark-ab-r8g8.txt` (series of 30.09: base / gc / gc_r8g8 /
tp plus a separate clear series of 6 pairs).

### 15.2 Synthetic: what enabling the gen3 flags gave

Series of 30.09, average over 2 runs:

| scene | base | gc | gc_r8g8 | tp | gc | gc_r8g8 | tp |
|---|---|---|---|---|---|---|---|
| | | | | | delta vs base | | |
| clear | 0.057 | 0.058 | 0.058 | 0.058 | +0.9% | +1.8% | +1.8% |
| vertex | 0.141 | 0.052 | 0.053 | 0.141 | **−63.1%** | −62.1% | +0.0% |
| shading | 0.158 | 0.061 | 0.057 | 0.159 | **−61.1%** | −64.2% | +0.3% |
| texture | 0.153 | 0.054 | 0.054 | 0.153 | **−64.5%** | −64.5% | −0.3% |

- **`has_generic_clear` is the only flag with an effect.** An important detail:
  the `clear` scene itself doesn't get faster (+0.9%); vertex/shading/texture
  do. The reason is not the clearing itself but that generic clear disables
  conditional load/store (`tu_pass.cc:560`): the "clear and draw" pass stops
  loading the previous render target contents.
- **`r8g8_faulty_fast_clear_quirk` in a pair with gc is free**: gc_r8g8 ≈ gc in
  all scenes, i.e. the gain doesn't come from removing the R8G8 protection. The
  previously observed −25…−39% were inflated by an old baseline (vertex 0.210
  versus today's 0.141).
- **`enable_tp_ubwc_flag_hint` gives 0 on a740.** The "+1.8%" on clear is
  0.001 ms on a 57 µs frame, i.e. vkmark's quantization: a separate series of 6
  pairs on the clear scene gave 0.1433±0.0029 (base) versus 0.1438±0.0028
  (tp), delta +0.3% at t≈0.3.
- `has_gmem_vpc_attr_buf` (series of 29.09) — within noise (0–4%), the combo
  gives nothing beyond gc alone.

### 15.3 Check for corruption: test and picture

- **Functionally** — `scripts/run-test.sh` with `TU_FORCE_PROPS`:
  `results/test-props2-{base,cs_lock_unlock_quirk,enable_tp_ubwc_flag_hint,
  r8g8_faulty_fast_clear_quirk,gc,gc_r8g8}.log`. All six give PASS and a
  byte-for-byte identical result to base; base on the new binary matched the
  old base, i.e. there is no drift.
- **Visually** — `scripts/shot.py` + `scripts/pixelcheck.py`, scheme
  base1/flag/base2 **interleaved**: comparing screenshots with a ten-minute gap
  yields false positives (wallpaper change, panel). Results: gc and gc_r8g8 —
  0 changes on clear, 1 pixel out of 880 on texture (delta 6/255 on an animated
  edge); tp — 0 of 880 in both scenes. Black screenshots in the series turned
  out to be a race (vkmark exited before the shot) — fixed by `--run-forever`
  plus a `kill -0` check.

### 15.4 RE4: no gain, and tp produces artifacts

Protocol: `HUDLOG=120 scripts/run-game.sh patched 150`, the segment is walked
by hand, the metric is `p25` over frames with `fps < 30` (section 13). Three
runs in a row so that the compared segment is identical (a fresh baseline is
needed precisely for this: the old 13.9 is a different day and a different
playthrough).

| run | fps p25 | median | mean | n |
|---|---|---|---|---|
| base | 11.0 | 12.4 | 12.33 | 73 |
| `enable_tp_ubwc_flag_hint` | 11.0 | 12.5 | 13.34 | 66 |
| `has_generic_clear,r8g8` | 11.5 | 12.4 | 12.50 | 77 |

CSV: `results/fps-abbase.csv`, `fps-abtp.csv`, `fps-abgc.csv`; logs
`results/game-patched-ab{base,tp,gc}.log`. The force reached the driver — in
`results/raw/game-patched-ab{tp,gc}.log` there are lines
`TU_FORCE_PROPS: <name>` (in base there are none).

- **`gc+r8g8`: median −0.4%, i.e. zero.** In vkmark the `clear` scene consists
  entirely of clears, and they account for the whole frame; in the game a frame
  is ~75 ms and is bound by memory bandwidth (section 13), while clears are a
  small part of it. −60% on a small share gives no noticeable gain overall.
- **`enable_tp_ubwc_flag_hint=True`: artifacts on screen** (screenshots
  `/tmp/opencode/shots/gametp-artifact-{1,2,3}.png`; in base they are absent).
  vkmark didn't show them because it doesn't do scaling blits — and
  `BLIT_OP_SCALE` is exactly what the prop's comment mentions
  (`freedreno_dev_info.h:394`). The prop is not "empty": on a740 it must
  remain `False` (the value per the v6xx blob).

### 15.5 Autotune, draw states and preamble series (30.09)

The second series of env switches, this time without rebuilding the driver.
Protocol as in 15.4 (`HUDLOG=120`, `p25` over frames with `fps < 30`), two
rounds (the second in reverse order), configurations interleaved with the
baseline within the day. Baselines over the day: 12.2 / 12.7 / 12.6, i.e. the
series noise is ±0.3. vkmark of the same series
(`results/vkmark-ab-autotune.txt`) is entirely within noise: 0.053–0.059 ms on
all four configurations.

| config | p25 (mean) | runs | Δ vs baseline |
|---|---|---|---|
| base | 12.5 | 12.2, 12.7, 12.6 | — |
| `TU_AUTOTUNE_ALGO=profiled` | 12.6 | 12.5, 12.7 | 0 |
| `TU_AUTOTUNE_FLAGS=big_gmem` | 11.05 | 11.1, 11.0 | **−1.5** |
| `TU_MAX_DRAW_STATES=64` | 12.7 | 12.7, 12.7 | 0 |
| `64 + TU_AUTOTUNE_FLAGS=preempt_optimize` | 12.6 | 12.6, 12.6 | 0 |
| `TU_FORCE_PROPS=load_inline_uniforms_via_preamble_ldgk` | 12.6 | 12.6, 12.6 | 0 |

CSV: `results/fps-at-*.csv` (the mapping of names to configs is in
`results/series-autotune.txt`; the mislabeled `fps-at-bigb.csv` is an extra
baseline run), logs `results/game-patched-at-*.log`.

- **`big_gmem` is consistently 1.5 frames worse (~12%)**: occupying more GMEM
  on this game's pass is harmful. Anti-recommendation.
- **`profiled` = 0**: the profiled autotune algorithm finds nothing better than
  the baseline — the tiling decisions are already correct.
- **`TU_MAX_DRAW_STATES=64` is a no-op by itself.** The prop
  (`max_draw_states`, "The amount of valid draw state IDs",
  `freedreno_dev_info.h:484`; a740 inherits 32 from `a6xx_base`,
  `freedreno_devices.py:152`, the gen3 template sets 64 at `:901`) is read in
  the tree in exactly one place — `tu_autotune.cc:339`: it gates
  `mod_flag::PREEMPT_OPTIMIZE` among the supported autotune flags, with the
  condition `max_draw_states > TU_DRAW_STATE_AT_WRITE_RP_HASH`. The threshold
  is `COUNT+1` = (19 static + 13 dynamic) + 1 = **33**: at 32 the condition is
  false (the flag is cut out at assignment on `tu_autotune.cc:319`), at 64 it
  is true. So 64 is exactly the number with which gen3 unlocks the autotune
  preempt optimization, and the separate `TU_MAX_DRAW_STATES` variable is
  needed because the prop is numeric (`tu_device.cc:1708`).
- **Combo `64 + preempt_optimize` = 0** — the flag itself doesn't move the
  frame.
- **`load_inline_uniforms_via_preamble_ldgk` = 0.** Correctness before the
  measurement: `results/test-preamble-iu.log` matches
  `results/test-props2-base.log` byte for byte (comparison without the
  `TU_FORCE_PROPS` lines). `load_shader_consts_via_preamble` was not measured
  in the series: its corruption was reproduced by a separate run — all reads
  `0x00000000`, section 14.3.

### 15.6 drirc: `tu_override_uncached_as_cache_coherent`

This option is not in `TU_FORCE_PROPS` — it is a driconf one, and in **all**
game logs, including the stock driver, there are lines
`ATTENTION: default value of option ... overridden by environment`. The source
was found: FEX inside proton-cachyos-11.0-arm64 calls
`__wine_set_unix_env("tu_override_uncached_as_cache_coherent", "true")`
(the string is baked into `files/lib/wine/aarch64-windows/libwow64fex.dll` and
`libarm64ecfex.dll`), i.e. the value is always `true`, regardless of the
driver. FEX doesn't overwrite its own `false` (verified via
`/proc/<pid>/environ` of the wine processes), so A/B is possible with a plain
environment variable.

Semantics (`tu_device.cc:1887`, `tu_knl.cc:57`): with the option, every
allocation requested as `DEVICE_LOCAL|HOST_VISIBLE|HOST_COHERENT`
("uncached" by default — this catches vkd3d-proton's UPLOAD heaps) is redirected
to the `+HOST_CACHED` type. The type exists (vulkaninfo: `memoryTypes[1]` =
`DL|HV|HC|CACHED`), i.e. the option is "live".

Protocol as in 15.5, interleaved within the day:

| run | p25 | median | n |
|---|---|---|---|
| `=false` (offA) | 11.6 | 11.6 | 90 |
| default, `true` (base6) | 12.6 | 12.7 | 90 |
| `=false` (offB) | 11.7 | 11.7 | 88 |
| default, `true` (base7) | 12.6 | 12.7 | 87 |

**Turning the option off is 1.0 frames worse (~8%)**, both switches agree, the
series noise is ±0.3. Cached-coherent host memory under FEX is faster than
uncached — the option must not be turned off, there is nothing left to measure.
CSV: `results/fps-at-{offA,base6,offB,base7}.csv`, series —
`results/series-drirc.txt`.

### 15.7 Summary

No gen1/gen3 property needs to be enabled on a740. Of the 28 gen3 and 5 gen1
properties in `TU_FORCE_PROPS`, only 10 actually apply (list —
`docs/a7xx-gen-props.md`, section "Coverage"), and per them:

| category | flags | grounds |
|---|---|---|
| breaks | `load_shader_consts_via_preamble`, `ubwc_all_formats_compatible` | corruption in the test (sections 14.3 and 14.5) |
| 0 / noise | `has_gmem_vpc_attr_buf`, `ubwc_coherency_quirk`, `has_compliant_dp4acc`, `has_persistent_counter`, `has_abs_bin_mask`, `cs_lock_unlock_quirk` | test and vkmark show no differences |
| synthetic, 0% in game | `has_generic_clear`, `r8g8_faulty_fast_clear_quirk` | sections 15.2 and 15.4 |
| 0 in game | `load_inline_uniforms_via_preamble_ldgk` | test ok, measurement: section 15.5 |
| corruption in game | `enable_tp_ubwc_flag_hint` | section 15.4 |

Also checked were not props but algorithm/size switches (autotune,
`TU_MAX_DRAW_STATES`, the driconf `tu_override_uncached_as_cache_coherent`)
— all zeros except the harmful `big_gmem`; the drirc option is on the contrary
useful (turning it off is 8% worse) — sections 15.5 and 15.6.

The remaining gen3 properties aren't in `TU_FORCE_PROPS` at all — they can only
be enabled by editing `freedreno_devices.py`. Per the code, two of them don't
apply to a740: `new_control_regs` (on a750 the control register layout is
permuted) and `has_sw_fuse` (it would read SW_FUSE and blow away raytracing,
`tu_device.cc:204`). `has_alias_rt` and `has_rt_workaround` haven't been
checked by anything: we have no ray tracing test.

Of the gen1-only ones (`cs_lock_unlock_quirk`, `stsc_duplication_quirk`) the
first was checked and gave nothing; the second cannot be checked in principle:
`TU_FORCE_PROPS` only does `True`, and ours is `True` already (gen2).

Since 30.09.2026 the pair `has_generic_clear` + `r8g8_faulty_fast_clear_quirk`
is enabled for gen2 **by default** — patch 0008
(`patches/0008-turnip-gen2-generic-clear.patch`, editing `a7xx_gen2` in
`freedreno_devices.py`); the env force of the pair is no longer needed. Check
after the rebuild (`results/test-gen2gc.log`): the test matches byte for byte
both `test-props2-base.log` (default before the edit) and
`test-props2-gc_r8g8.log` (forced pair) — only the `TU_FORCE_PROPS` lines
differ; the `FAIL` lines are the same 6 as in all the references. The generated
table: FD740 `(True, True)`, FD730 `(False, False)` — gen1 untouched, FD750
`(True, True)` — as before. The picture for the pair is the `gc-*`/`gcr8-*`
screenshots from 15.3.

## 16. mixed-swap (cross-order experiment): tiled+UBWC for {BGRA8, RGBA8}

> Here and below "cross-order experiment" is **not patch 0006** (that one adds
> `FD6_UBWC_B8G8R8A8_INT`, see §5 and `patches/`), but the probe
> `experiments/exp-6-cross-order-list-tiled-ubwc.patch`. Experiments are
> numbered `exp-N`, patches `000N`: which experiment went into which
> patch - the lineage table in the main README.

One reinterpretation scenario remained unused: a format list with the same
block geometry but a different channel order —
`{B8G8R8A8_UNORM, R8G8B8A8_UNORM}`. The control (stock and patches
0004–0008): `swaps_are_uniform()` rejects such a list → `tu_image_init()`
turns off UBWC and forcibly puts the image in linear. The cross-order
experiment (`experiments/exp-6-cross-order-list-tiled-ubwc.patch`)
introduces the predicate `tu6_format_list_same_shape()` (all formats of the
list are the same texel block: channels, bits, shape) and in the mutable branch
leaves such a list tiled+UBWC; the sparse query
(`tu6_mutable_format_list_forces_linear`) is deliberately not touched.

The check is a new probe **S3** in `rp6-vkd3d-sparse-test.c` (non-sparse
MUTABLE `{BGRA8, RGBA8}`: fill, read with the base BGRA view, read with the
cross view RGBA, raw transfer). Logs `results/test-s3-*.log`:

| Run | memreq | base BGRA | cross RGBA | raw |
|---|---|---|---|---|
| stock 26.2.3 (`stock`) | 0x100000, linear | EXACT | identity (`== fill2`) | 0/262144 |
| control 0004–0008 (`control`) | 0x100000, linear | EXACT | identity | 0/262144 |
| experiment (`ubwc`) | 0x102000, tiled+UBWC | EXACT | **swapped** (`b0<->b2`) | 0/262144 |
| after rollback (`postrev`) | 0x100000, linear | EXACT | identity | 0/262144 |

What follows from this:

- **Mechanically the HW copes**: raw bytes pass through
  compress-on-store/decompress-on-copy without loss, the base view reads
  exactly, the cross view is deterministic — there is no garbage.
- **But the view's semantics changes.** Identity is positional
  reinterpretation of bytes: the RGBA view reads memory byte0 as Red. Both the
  linear path and stock give this. On UBWC the cross view returned the same
  channels as the base (swapped) — the view's channel order is ignored, the
  behavior deviates from stock, against which the other probes were verified.
  (The S3 PASS criterion is deliberately structural — "one of the two
  candidates"; the correctness decision is made by comparison with the
  control.)
- **There is no practical gain**: vkd3d-proton doesn't build cross-order lists —
  0 of 72 in `tests/vkd3d-fmt-classes.h`. This is a consequence of the code,
  not just of parsing the header: the list comes from a single table
  `dxgi_format_compatibility_list[]` (`libs/vkd3d/utils.c:457+`, copy of the
  sources in `srcs/vkd3d-proton`), where the classes mirror DXGI-typeless
  families and don't mix channel order (the cast `R8G8B8A8 ↔ B8G8R8A8` is
  forbidden in D3D12), and the runtime additions
  (`vkd3d_format_compatibility_list_add_format`, 5 places in `resource.c`) add
  only the image's base format, the view formats of the same entry, the
  R32-UAV set and `R64_UINT`.
- Incidentally, sparse-create opened up for such lists: `E` and `S2` changed
  `FEATURE_NOT_PRESENT` to `SUCCESS` (as on stock), while the query stayed
  `FORMAT_NOT_SUPPORTED` — create and query diverged. `F` (NULL list) was not
  touched, the rest of the test output didn't change (the same 6 `FAIL`s).

Summary: rollback via `git apply -R experiments/exp-6-cross-order-…`,
rebuild the driver, a control run matched the control byte for byte
(`postrev` == `control`), the mesa tree files were compared with the backup —
identical. Conclusion: "cross-order tiled+UBWC" is mechanically achievable in
HW, but the observed cross-view semantics is not equal to the stock/linear one
→ do not enable. S3 stays in the test as a control of this boundary.

### 16.1. Who builds cross-order lists at all (checked against the code)

The absence of such lists in vkd3d is neither coincidence nor a shortcoming but
a consequence of the D3D12 specification. In `dxgi_format_compatibility_list[]`
(`srcs/vkd3d-proton/libs/vkd3d/utils.c`) the classes mirror the typeless
families, and BGRA/RGBA are two **non-overlapping** classes:

```c
// utils.c:317 — RGBA8 family: only variants of one channel order
{DXGI_FORMAT_R8G8B8A8_TYPELESS,
        {R8G8B8A8_UINT, R8G8B8A8_SINT, R8G8B8A8_UNORM,
         R8G8B8A8_UNORM_SRGB, R8G8B8A8_SNORM}, …},

// utils.c:457 — BGRA8 family, separately
{DXGI_FORMAT_B8G8R8A8_TYPELESS,
        {B8G8R8A8_UNORM, B8G8R8A8_UNORM_SRGB}, …},
```

UNORM/SNORM/UINT/SINT are compatible because they interpret the same bits the
same way; `R8G8B8A8 ↔ B8G8R8A8` are not — the channel order differs, and D3D12
doesn't allow such a cast.

The second consumer of mutable lists is **Zink** (a GL gateway into Vulkan,
i.e. into our turnip; `src/gallium/drivers/zink/zink_resource.c:1561`,
`setup_format_list()`) — it builds exactly two kinds of lists:

```c
if (!(templ->bind & ZINK_BIND_MUTABLE) && can_srgb) {   /* linear ↔ srgb */
   srgb = util_format_is_srgb(templ->format) ? util_format_linear(templ->format)
                                             : util_format_srgb(templ->format);
}
if (srgb) { formats[0] = zink_get_format(…, templ->format);
            formats[1] = zink_get_format(…, srgb); }      /* same channel order */
else if (templ->bind & ZINK_BIND_VIDEO) { … }            /* planes of one format */
```

So it is always `format + its sRGB variant` or the planes of one format —
**the channel order is the same**, there is no cross-order.

Summary: cross-order lists are unreachable both for D3D12 and for GL through
Zink.

About the Zink pairs `format + sRGB variant` — see §16.2: they stay
tiled+UBWC even on stock, the code never reaches `has_swaps`. Neither the
cross-order list nor the uniform-swap list for which patch 0005 was written is
built by either D3D12 or Zink.

### 16.2. Who actually ends up in linear: an enumeration accounting for the early exit

The first analysis in §16.1 gave an incorrect conclusion ("0005 fixes the Zink
pairs `RGBA8 ↔ RGBA8_SRGB`"). The reason was a simplified enumeration that only
counted `has_swaps()`. The decisive detail it missed: **`tu6_mutable_format_list_ubwc_compatible()`
sits above `has_swaps()` and skips the whole block**, and this gate exists in
stock:

```c
// tu_image.cc:539 — stock, tu_image.cc:530 — git HEAD, without our edits
if ((pCreateInfo->flags & VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT) &&
    !vk_format_is_depth_or_stencil(image->vk.format)) {
   const VkImageFormatListCreateInfo *fmt_list = …;
   if (!tu6_mutable_format_list_ubwc_compatible(info, fmt_list)) {
      … NV12 branch, UBWC removal, r8g8_r16, has_swaps …
   }
}
```

The same early exit exists in `tu6_mutable_format_list_forces_linear()`
(`tu_formats.cc:230`), i.e. in the sparse query of patch 0004 — it too is
consistent with create. List compatibility is decided by `fd6_ubwc_compat_mode()`
(`freedreno_ubwc.h`), where the `linear` variant and the `_SRGB` variant of the
same format always give **the same** type.

FD740 properties: `freedreno_devices.py` → `[a7xx_base, a7xx_gen2]`, i.e.
`ubwc_unorm_snorm_int_compatible = True`, `ubwc_all_formats_compatible = 0`.
Enumeration: `scripts/fmt-list-gate-check.py` (ports the driver's predicates to
Python, taking the properties from `fd6_format_table.c` and
`fd6_ubwc_compat_mode()`).

**vkd3d, 70 classes of `tests/vkd3d-fmt-classes.h` (at the time; about the two
classes added later `B8G8R8A8/B8G8R8X8_TYPELESS+UAV` — §21.3):**

| Fate | Classes | create on stock | +0005 | +0005+0006 |
|---|---|---|---|---|
| early exit, list UBWC-compatible | 31 | tiled+UBWC | same | same |
| gate didn't pass, but no swaps | 38 | tiled, UBWC off | same | same |
| planar NV12 | 1 | tiled, UBWC off | same | same |
| **into linear** | **0** | — | — | — |

**No class from `tests/vkd3d-fmt-classes.h` reaches linear on gen2 even on
stock.** The six BGRA classes (`tests/vkd3d-fmt-classes.h:51-56`) are
`{B8G8R8A8_UNORM, B8G8R8A8_SRGB}`, both formats give `FD6_UBWC_B8G8R8A8_UNORM`,
the gate passes, and stock deliberately contains this case with the comment
"zink would really like to". The earlier "6 BGRA classes in linear" was an
enumeration error.

**But there is a path into linear in D3D12, and the table above doesn't model
it.** `vkd3d_get_format_compatibility_list()` (`utils.c:393-401`) for a
TYPELESS format with `byte_count == 4` under the `ALLOW_UNORDERED_ACCESS` flag
adds `{R32_UINT, R32_SINT, R32_SFLOAT}` to the list — this is the D3D11 rule
about typed UAV loads. For `B8G8R8A8_TYPELESS + ALLOW_UNORDERED_ACCESS` you get
a mixed list of five formats:

```
B8G8R8A8_UNORM + B8G8R8A8_SRGB + R32_UINT + R32_SINT + R32_SFLOAT
  gate: stock=False  0006=False  0010=False   ->  linear
```

The answer here is correct: BGR and R32 are different layouts, a common compat
class doesn't fit them, linear is legitimate. But it followed that the claim
"on gen2 no vkd3d image goes into linear" holds only for the 70 classes listed
at the time, not for vkd3d in general: `tests/vkd3d-fmt-classes.h` contained no
R32 additions to the BGRA classes, i.e. the model didn't check this case.

Later these two classes were added to the model (`B8G8R8A8_TYPELESS+UAV`,
`B8G8R8X8_TYPELESS+UAV`) and measured on hardware — section 21. It turned out
that the "linear is legitimate" rule is excessive here: only single-channel
formats diverge, which have no channel order at all, so changing visibility
through
`R32_UINT` view is nothing to change. `experiments/exp-9-single-channel-swap-vacuous.patch`
removes this rule, and tiling returns on the 740. Neither 0005 nor 0006 closes
this case, see §21.3.

**Zink.** `setup_format_list()` (`zink_resource.c:1561`) builds exactly two
kinds of lists — `(F, F_SRGB)` and the planes of a video format. Of the 11
pairs `(F, F_SRGB)` that exist in the format table at all:

| Pairs | stock create | +0005 | +0006 |
|---|---|---|---|
| `R8G8B8A8`, `R8G8`, `B8G8R8A8` (3) | tiled+UBWC (early exit) | same | same |
| `A8B8G8R8`, `A8R8G8B8`, `B8G8R8X8`, `X8B8G8R8`, `X8R8G8B8` (5) | **linear** | tiled | linear |
| `L8`, `R8`, `R8G8B8X8` (3) | tiled, UBWC off | same | same |

But Zink doesn't build these five pairs either. GL hands Zink only
`B8G8R8A8_UNORM` from this set (`st_format.c:268`, `{ GL_BGRA, GL_BGRA8_EXT, 0 }`),
and its pair passes the gate. `PIPE_FORMAT_A8R8G8B8_*`, `A8B8G8R8_*`,
`X8R8G8B8_*`, `X8B8G8R8_*`, `B8G8R8X8_*` don't appear even once in
`src/gallium/drivers/zink/` — only inside `zink_format_emulate_x8()`, i.e. as a
result of emulation rather than as a resource format.

The third Zink case is `ZINK_BIND_MUTABLE`. Here the format list is
**absent**: `init_ici()` (`zink_resource.c`) sets `MUTABLE_FORMAT_BIT` and
zeroes `pNext`; `emit_usage_candidates()` under `always_mutable` doesn't build
a fmtlist variant either. Such an image → linear without UBWC on stock and with
the patches, and this is correct: no list is set, so the application is entitled
to reinterpret the image into any compatible format, and
`has_swaps(NULL) == true` by stock design. The x8/alpha-emulation pairs
(`zink_format_needs_mutable()`, `zink_format.h:53`) give `false`, i.e. mutable
isn't needed for them at all.

**Who 0005 actually fixes.** Enumeration of all ordered format pairs of the
table (`--all-pairs`, 115×114):

- **0005** restores tiling for **158** pairs — exclusively the BGR/BGRA family
  (`B5G6R5 ↔ B5G5R5A1`, `B5G6R5 ↔ B8G8R8A8`, …). Of those, **18** pairs are
  also covered by 0006 (`B8G8R8A8` with `UNORM/SRGB/SNORM/UINT/SINT`, where UBWC
  is also restored), the remaining **140** — only by 0005.
- **0006** — 18 pairs: exactly `{B8G8R8A8_X, B8G8R8A8_Y}` for all `X ≠ Y` from
  `{UNORM, SNORM, UINT, SINT, SRGB}`.
- `r8g8_r16` doesn't fire on any pair of the table.

**Checked against the consumer's code, not by enumerating pairs.** Previously
here stood "vkd3d translates DXGI-BGR into RGBA order, therefore it doesn't
build lists". This is true only for 16-bit BGR (`B5G6R5 →
VK_FORMAT_R5G6B5_UNORM_PACK16`, `B5G5R5A1 → A1R5G5B5_UNORM_PACK16`,
`B4G4R4A4 → A4R4G4B4_UNORM_PACK16`, `A4B4G4R4 → R4G4B4A4_UNORM_PACK16` —
`utils.c:114-138`), while 32-bit BGRA8 vkd3d doesn't touch: `utils.c:107-112`
hands out `VK_FORMAT_B8G8R8A8_UNORM` directly. Therefore the B8G8R8A8 family
lists must be analyzed separately:

* In `dxgi_format_compatibility_list` (`utils.c:279-303`) the B8G8R8A8 entries
  **do exist**, and the only list vkd3d builds from them is
  `{B8G8R8A8_UNORM, B8G8R8A8_SRGB}` (`utils.c:279-282`). Both formats give one
  compat type `FD6_UBWC_B8G8R8A8_UNORM`, the gate passes on stock, and
  0006/0010 change nothing on this pair: `tiled=True ubwc=True` in all four
  combinations.
* 0006 adds `UINT`/`SINT`/`SNORM` to this class, but such DXGI formats don't
  exist in vkd3d **at all** — `grep 'B8G8R8A8_{UINT,SINT,SNORM}' libs/vkd3d/utils.c`
  is empty. vkd3d cannot build the list for which 0006 was written.
* 0005 fixes the cross pairs of 16-bit BGR, and Zink doesn't know them:
  `PIPE_FORMAT_{A8R8G8B8,A8B8G8R8,X8R8G8B8,X8B8G8R8}` don't appear even once in
  `src/gallium/drivers/zink/`; `B8G8R8X8`/`B8G8R8A8` — two occurrences each,
  both inside `zink_format_emulate_x8()`, i.e. the result of emulation rather
  than a resource format.

Summary: **on gen2 neither 0005 nor 0006 changes a single image, neither for
D3D12 nor for GL/Zink.** Both are logically correct and implement the upstream
TODO, both are safe, but their only consumer is our own synthetic probes S and
T. This was verified by enumeration over the 72 classes, including the two
added later `B8G8R8A8/B8G8R8X8_TYPELESS+UAV`: they don't reach 0005/0006, for
the reasons analyzed in §21.3. There is no point in keeping them in the working
stack for ArmadOS; `0004` (removing the sparse gate) and `0009` (NV12) are
another matter — they change behavior on real lists.

**A side risk of 0004 found in this analysis.** vkd3d creates `MUTABLE`
**without** a format list in three places (`libs/vkd3d/resource.c`): planar
formats (`:383`, `:363` — `VK_IMAGE_ASPECT_PLANE_0_BIT`), compatibility list
overflow (`:365-368`, needing 10 formats out of
`VKD3D_MAX_COMPATIBLE_FORMAT_COUNT = 10`) and bypassing
`DISABLE_SIMULTANEOUS_UAV_COMPRESSION` (`:826`). The first case on gen2 is
NV12, and 0009 closes it. The other two give linear + sparse → our 0004
rejects create (`FEATURE_NOT_PRESENT`, exactly like probe F), i.e. vkd3d gets
an error instead of silent corruption. This is a deliberate trade: upstream
instead of rejecting simply doesn't advertise `sparseResidencyImage2D`.

## 17. RE4 memory measurement (30.09)
Method: run `scripts/run-game.sh patched 150` with
`VKD3D_CONFIG=log_memory_budget VKD3D_DEBUG=info VKD3D_LOG_FILE=...`
(vkd3d prints every allocation: size + running total per memory type, after
each — a `Memory heap #0 ... usage` report from VK_EXT_memory_budget),
`TU_DEBUG=bos` (top of turnip's live BOs; printed after every submit, therefore
the raw 4,215,096 lines / 242 MB stayed in results/raw/, only the last dump in
git) and MangoHud CSV (`ram_used`/`swap_used`). Artifacts:
`results/vkd3d-mem.log`, `results/game-patched-mem.log`,
`results/fps-at-mem.csv`.

### Numbers

| source | result |
|---|---|
| vkd3d: peak / end | 3129 MiB / 3108 MiB (720 allocations) |
| budget | the driver declares a budget of 2672 MiB at usage 3122 MiB — vkd3d's non-budgeted path bypasses it |
| vkd3d top | chunks 16 MiB ×66 = 1056 MiB (VA chunks for small buffers), paired heaps 187500 KiB ×2 and 133640 KiB ×2, then 128/50/42.7 MiB |
| turnip: live BOs | 3490 MiB, internals ~370 MiB |
| — `pipeline_suballoc` | 1798 BO = 225 MiB |
| — `pvtmem` | ×3 = 120 MiB (fiber/wave private memory, grows in powers of two, tu_shader.cc:2871) |
| — `embedded samplers` | 3025 BO = 12 MiB |
| system | `ram_used` mean 6.76 / max 7.24 of 7.32 GiB, `swap_used` max 4.95 GiB |

### Conclusions

1. The main volume is the game's own resources (≈3 GiB): the only big lever is
   the in-game texture/quality settings.
2. Driver internals ~370 MiB are secondary; `pipeline_suballoc` and `pvtmem`
   are areas where reduction is only possible by editing the driver (`pvtmem`
   is set by shader requirements, it can't be cut by force).
3. There are no env levers for memory — confirmation of §15: all the env moves
   neither FPS nor footprint. `VKD3D_SWAPCHAIN_IMAGES=2` (the swapchain is by
   default `max(3,minImageCount)`, swapchain.c:2207) yields single-digit MB.
4. The system lives on borrowed time (swap max 4.95 GiB with sum ≈ 10 GiB);
   swappiness=180/zram — root only, outside the project's scope.
5. `TU_DEBUG=bos` must not be used in repeated measurements: a dump after every
   submit = 242 MB of log.

## 18. NV12 + MUTABLE: `force_linear_tile` is redundant (patch 0009)

### 18.1 What was noticed

The planar NV12 branch in `tu_image.cc` for images with
`VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT` set two restrictions at once —
`ubwc_enabled = false` **and** `force_linear_tile = true`. The second
restriction made creating NV12 + MUTABLE + SPARSE_RESIDENCY impossible: the
gate from patch 0004 rejects on create at `tile_mode != TILE6_3`, because the
sparse block addressing describes a tiled layout. This was the only path from
vkd3d into the 0004 rejection, which closed the question "is a vkd3d patch
needed for the planar branch" (sections 4 and 6).

### 18.2 Why the tiling need not be removed

The argument from reading the code, later confirmed by measurement:

- `tu_image_view_init` reinterprets NV12 plane 0 as Y8/R8, and the code comment
  names the reason as a difference in **compression**: "the 0'th plane of this
  format has a different UBWC compression". So the reinterpretation itself is
  defined as identity in terms of layout.
- The layout is built by exactly the same format: `tu6_plane_format()` returns
  `PIPE_FORMAT_Y8_UNORM` for plane 0, and that same value goes into
  `tu_image_init`. For plane 1 the base format matches (`R8_UNORM` /
  `PIPE_FORMAT_R8_UNORM`).

Only what the data is compressed with changes, not the geometry of the planes.
So `ubwc_enabled = false` is sufficient, and a separate restriction on tiling
is not.

### 18.3 Measurement

Probe **N** in `rp6-vkd3d-sparse-test.c`. The key line is N2, create
NV12 + MUTABLE + SPARSE_RESIDENCY:

| build | N2 | N1 (without MUTABLE) |
|---|---|---|
| stock 26.2.3 | SUCCESS | SUCCESS |
| 0004–0008 | `FEATURE_NOT_PRESENT` | SUCCESS |
| 0004–0008 + **0009** | **SUCCESS** | SUCCESS |

Logs: `results/test-nv12-linear-patched.log` (0004–0008) and
`results/test-nv12-keep-tiling.log` (+0009).

An indicator independent of size computation: the gate in `tu_image.cc`
rejects exactly at `tile_mode != TILE6_3`, so `SUCCESS` on N2 means TILE6_3
directly, without interpreting memreq.

### 18.4 An error in probe W, fixed along the way

Probe W was meant to be the deciding one and **did not do its job**: it printed
"linear NV12 128x128 would be 0x6000" and on both stock and 0009 it reported
`memreq 0x6000`. So tiled without UBWC gives exactly the same amount, and by
that number linearity cannot be distinguished from tiled. The caption was a
false proof; in the test it was replaced by an explicit warning, and the W
verdict was replaced with neutral reference numbers.

A second reason, recorded in the code: reading "plane 0 through an
R8_UNORM view" by copying is **inexpressible** — `vkCmdCopyImageToBuffer`
takes a `VkImage`, not a `VkImageView`, so transfer doesn't distinguish views
at all. A complete proof would require a shader sampling the R8 view; that is
separate work, it hasn't been done and isn't used in the conclusion.

### 18.5 Summary

- **vkd3d doesn't need patching.** The only path from vkd3d into the 0004
  rejection was closed on the driver side.
- NV12 + MUTABLE stops being linear. For video this is a normal configuration,
  and the demotion cost bandwidth and tiling.
- The edit is not in the per-chip gate but in common code, hence a candidate for
  upstream: a standalone `tu_image.cc` edit without a quirk gate.
- Rollback: `git apply -R patches/0009-turnip-nv12-mutable-keep-tiling.patch`.
  An incremental `scripts/rebuild-turnip-msm.sh` is needed, **but** with the
  image `mesa-build-fedora44-wsi-glslang` rather than
  `mesa-build-fedora44-full` (section 19).
## 19. The build image: `mesa-build-fedora44-full` is stale

### 19.1 Symptom

Incremental rebuild (`scripts/rebuild-turnip-msm.sh`) failed at link time,
listing missing libraries one after another:

```
ninja: error: '/usr/lib64/libX11-xcb.so', needed by
  'src/freedreno/vulkan/libvulkan_freedreno.so', missing and no known rule
```

### 19.2 Cause

`docs/analysis.md` prescribes making the image via
`podman commit mesa localhost/mesa-build-fedora44-full` — the image is not
rebuilt, and `commit` freezes the container in whatever state it ended up in.
Measured contents:

| image | date | of the 6 WSI-devel packages |
|---|---|---|
| `mesa-build-fedora44-full` | 27.09 | **0** |
| `mesa-build-fedora44-wsi-glslang` | 29.09 | **6** |

All six packages (`libxcb-devel libX11-devel libXrandr-devel
libxshmfence-devel xcb-util-keysyms-devel wayland-devel`) are listed explicitly
in `container/Containerfile.mesa-build`, together with the note about name
casing. So **the documentation is correct — the image merely fell behind it**:
the commit was made before the WSI block was added to the Containerfile.

Why it wasn't caught immediately: a full rebuild (`scripts/build-turnip.sh`
with `rm -rf build`) recreates the directory and `meson setup` finds the
dependencies itself. An incremental rebuild reuses the existing `build/`, where
meson has already written absolute paths of the form
`/usr/lib64/libX11-xcb.so`. Those paths haven't gone anywhere, but the packages
aren't in the image.

### 19.3 What to do

Take `localhost/mesa-build-fedora44-wsi-glslang` for any rebuild. Or rebuild
the image from `container/Containerfile.mesa-build` so that it cannot diverge
from the Containerfile again.

Separately: `podman run --rm` discards what was installed, so "install the
packages once into the image" doesn't work in principle — if the image isn't
rebuilt, the dependencies are installed in the same run. And `dnf` rolls back
**the entire** transaction on any wrong package name, silently going into
`>/dev/null`, so the list must be exact (in Fedora it is `libXrandr-devel`, not
`libxrandr-devel`; there is no `libxcb-present` or the like — everything is in
`libxcb-devel`).

### 19.4 What was measured

| package | `-full` | `-wsi-glslang` |
|---|---|---|
| `libX11-devel` | no | yes |
| `libXrandr-devel` | no | yes |
| `libxshmfence-devel` | no | yes |
| `xcb-util-keysyms-devel` | no | yes |
| `libxcb-devel` | no | yes |
| `wayland-devel` | no | yes |

Check with one command:

```sh
podman run --rm localhost/<image> bash -c \
  'for p in libX11-devel libXrandr-devel libxshmfence-devel \
            xcb-util-keysyms-devel libxcb-devel wayland-devel; do
     rpm -q $p >/dev/null 2>&1 && echo "yes $p" || echo "NO   $p"
   done'
```

Casing matters in `rpm -q`: `libxrandr-devel` will return "no" even where the
package is installed — the name is `libXrandr-devel`.
---

## 20. The first fill into a fresh image loses ~2% of texels (a probe defect, not formats)

### 20.1 How it looked

Probe U (five TYPELESS classes of vkd3d, §20.4) initially gave MISMATCH on
almost all words, with `got=0x00000000`, and the mismatch count changed from
run to run. At first glance this looked like "UBWC breaks 32-bit formats",
which would have broken the whole reasoning about the gate.

### 20.2 Where the boundary is

Three controls, run through the same code as the main measurements, helped
isolate it:

| control | what we rule out |
|---|---|
| `linear` (no UBWC, no list) | compression isn't involved |
| `nonmut` (UBWC, no list) | the format list isn't involved |
| `CTRL_RGBA8`, `CTRL_BGRA8` (n=1) | the 32-bit float formats themselves aren't involved |

`CTRL_RGBA8` and `CTRL_BGRA8` are exactly the formats for which probe T-tr in
the same run gives `0/262144` mismatches with structurally identical code. So
the problem is in the probe, not in the driver and not in the formats.

Next, filling the readback buffer with the byte `0xCD` before copying showed:
`untouched = 0` with `zeros = 5120`. So `CopyImageToBuffer` wrote the whole
buffer, and the zeros really were in the image — i.e. **the fill itself didn't
finish writing**. The number of corrupted words was always a multiple of 64,
i.e. whole
64-texel chunks.

### 20.3 Cause

Extracted into a separate minimal program — `tests/repro-first-fill.c`:
512×512, `R8G8B8A8_UNORM`, only fill and readback, nothing from probe U. Each
run creates a **fresh** image and fresh memory, otherwise the second run hits a
warm image.

```sh
./repro-first-fill 3 cold   # one fill, measure immediately
./repro-first-fill 3 warm   # two fills in a row, measure after the second
```

```
cold:
rep0 OPTIMAL  memreq=0x102000: bad=5248/262144 zero=5248
rep0 LINEAR   memreq=0x100000: bad=5312/262144 zero=5312
rep1 OPTIMAL  memreq=0x102000: bad=5120/262144 zero=5120
rep1 LINEAR   memreq=0x100000: bad=5184/262144 zero=5184
rep2 OPTIMAL  memreq=0x102000: bad=5120/262144 zero=5120
rep2 LINEAR   memreq=0x100000: bad=4096/262144 zero=4096

warm:
all six lines bad=0/262144
```

The first `CopyBufferToImage` into a **newly created** image writes part of the
data as zeros. A second fill of the same image gives an exact picture. This
affects both `VK_IMAGE_TILING_OPTIMAL` and `VK_IMAGE_TILING_LINEAR`, i.e.
neither tiling nor UBWC is involved here. The format isn't involved either:
same result on `R8G8B8A8_UNORM`, which probe T-tr in the same binary measures
as EXACT.

Limitation of the finding: **the cause inside the driver has not been
established.** All that is known is "the first fill is incomplete, the second
is complete". It reproduces both on the stock driver and on 0004–0009 (log
`results/test-U-baseline-stock.log`, 14 MISMATCH), i.e. none of the project's
patches relates to it.

Practical conclusion for measurements: **a warm-up fill is needed before
measuring.** In probe U it was added; after it all lines give 0 mismatches.
This is the same class of artifact as the "usampler transform" from §14: a
wrong measurement setup, not a property of the hardware.

### 20.4 What was ultimately measured

After warm-up, on the stock driver and with the experiment
`experiments/exp-8-sfloat-int-ubwc-compat.patch`:

```
                              stock (0004-0006)          with patch
R32_TYPELESS          nonmut=0x102000 list=0x100000   list=0x102000
R16G16_TYPELESS       nonmut=0x102000 list=0x100000   list=0x102000
R32G32_TYPELESS       nonmut=0x202000 list=0x200000   list=0x202000
R16G16B16A16_TYPELESS nonmut=0x202000 list=0x200000   list=0x202000
R32G32B32A32_TYPELESS nonmut=0x404000 list=0x400000   list=0x404000
```

roundtrip (byte-wise, via transfer) — `0/262144`, `0/524288`, `0/1048576` in
all lines, both on stock and with the patch. No regressions: probes S and T —
PASS.

Logs: `results/test-U-baseline-stock.log` (before warm-up, 14 MISMATCH),
`results/test-U-baseline-warmup.log` (stock, warm-up, 0 MISMATCH),
`results/test-U-sfloat-gate-patched.log` (experiment, 0 MISMATCH).

A caveat about the `CTRL_*` lines: there `n=1`, the gate passes trivially, so
the `list` value on stock equals `nonmut`. In the first run the verdict was
printed as "NO (removed)" — that was a wording defect in the probe itself
(comparison `>` instead of `>=`). It didn't affect the measurement, nor the
numbers in the table above.

### 20.5 exp-8: `_FLOAT` into the integer compat classes

`experiments/exp-8-sfloat-int-ubwc-compat.patch` adds `_FLOAT`
members to `fd6_ubwc_compat_mode()` (`src/freedreno/common/freedreno_ubwc.h`):
`R16G16_FLOAT`, `R32_FLOAT`, `R32G32_FLOAT`, `R16G16B16A16_FLOAT`,
`R32G32B32A32_FLOAT` — into the same classes as their integer analogs.
`Z32_FLOAT` deliberately stays `UNKNOWN_COMPAT`: it belongs to no color
typeless family, and depth has its own clearing semantics.

What this gives in numbers: of vkd3d's 72 classes, 31 hold UBWC on stock, 36
with the experiment (`scripts/fmt-list-gate-check.py --vkd3d`, flag
`patch_0010`). The model and the measurement agreed on all five TYPELESS
classes.

The grounds are the layout, not the blob: UBWC encoding is determined by the
format class, and inside a class only channel sizes differ. An `_SFLOAT` member
has the same layout as the integer member of the same typeless format, and a
cast in Vulkan is a reinterpretation of the same bits, not a channel swap. The
stock comment `«a630 blob allows these, but not a660»` remains in force as an
indication that the blob table has no rows for float, not as proof of
incompatibility.

**What the patch does not check.** A byte roundtrip doesn't catch fast-clear:
the driver doesn't verify the contents of the clear metadata against the format
interpretation, so a wrong clear value will only show up where the application
does a fast-clear through a view in the float interpretation. To close this, a
probe is needed of the kind `vkCmdFillImage` with a color in float
interpretation → read raw bytes → compare with the same bit pattern
recomputed as integers. Until it exists, the caveat remains a risk; on
04.10.2026 exp-8 was nevertheless moved to
`patches/0012-turnip-ubwc-compat-sfloat-int.patch` — at the price of a caveat
recorded in the patch header (as with 0011).

## 21. The vkd3d list for typeless-BGRA8 under UAV: where tiling is lost and what MUTABLEEN does

The list that vkd3d-proton builds for typeless-BGRA8 under
`D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS`
(`d3d11_device.c` / `resource.c:393-401`, the D3D11 rule about typed UAV load):

```
B8G8R8A8_UNORM, B8G8R8A8_SRGB, R32_UINT, R32_SINT, R32_SFLOAT
```

Five formats, three of them single-channel. On stock the list goes to
**linear**, and these are the only 2 classes out of 72 that stock sends there
(`B8G8R8A8_TYPELESS+UAV` and `B8G8R8X8_TYPELESS+UAV`). Below is a measurement
of what happens with this list when tiling is kept after all.

### 21.1 How tiling was measured

Two detectors, both in probe `probe_mixed_swap_uav()` (tag `V`):

| detector | how | verdict |
|---|---|---|
| `memreq` at 512×512 | `vkGetImageMemoryRequirements` | **useless**: tile 64×64, 512×512 divides exactly 8×8, tiled and linear give the same `0x100000` |
| `memreq` at 500×500 | same | linear = `0xfa000` (row padded to 500×512×4), tiled = `0x100000` (padded to 512×512) — **distinguishable** |
| `SPARSE_RESIDENCY` + `TILING_OPTIMAL` | `vkCreateImage` | `SUCCESS` = tiling, `FEATURE_NOT_PRESENT` = linear (`tu_image.cc:660-664`) |

The tiling decision doesn't depend on the image size, so a 500×500 image sets
the same flag as the working 512×512. This is the only detector with which
**different driver versions** can be compared: the `SPARSE_RESIDENCY`
restriction under linear is `patches/0004-turnip-sparse-residency-gate-lift.patch`,
it doesn't exist upstream, and the system driver answers a sparse-create with
`SUCCESS` regardless of tiling.

A second important point about the sparse detector: the image for it must be
`TILING_OPTIMAL`. With `TILING_LINEAR` the result speaks about the modifier
choice rather than the format list, and the two detectors start contradicting
each other.

### 21.2 Three modes, one binary

Probe V makes two images with **the same** list: `t=0` — forcibly
`TILING_LINEAR` (reference), `t=1` — `TILING_OPTIMAL` (whatever the driver
decided). Filling via `vkCmdCopyBufferToImage` in the image's format
(`B8G8R8A8_UNORM`), reading — with the compute shader `cast_r32` through
`usampler2D` + an `R32_UINT` view, i.e. the **second** view is measured. The
BGRA8 side (fill and readback in the image's format) is measured by probe U,
class `B8G8R8A8_UAVLIST`.

| mode | 500×500 detector | UBWC | probe U (BGRA8 side) | probe V (R32 view vs linear) |
|---|---|---|---|---|
| system stock | `0xfa000` → **linear** | off | EXACT `0/262144` | matched `262144/262144` |
| stack 0004–0009 + exp. 0008 + **exp. 0009** | `0x100000` → **tiling** | off | EXACT `0/262144` | **diverged `261120/262144`** |
| same + **exp. 0010** (MUTABLEEN without UBWC) | `0x100000` → tiling | off | **MISMATCH `261119/262144`** | diverged `261120/262144`, byte-for-byte the same numbers as for 0009 |

Logs: `results/test-V-stock-linear.log`, `results/test-V-exp0009-tiled.log`,
`results/test-V-exp0009-0010-mutableen.log`.

Word values, for clarity:

| texel | written | linear (reference) | tiled |
|---|---|---|---|
| (0, 0) | `0x11223344` | `0x11223344` | `0x11443322` |
| (256, 256) | `0x11243444` | `0x11243444` | `0x11443424` |

In bytes this is a swap of byte 0 and byte 2: `[44,33,22,11] -> [22,33,44,11]`
and `[44,34,24,11] -> [24,34,44,11]`. So on tiling the linear→tile transition
lays out the four B8G8R8A8 components in tile order, and the `R32_UINT` view
hands out this same raw word with the swap.

### 21.3 exp-9: tiling obtained, BGRA8 untouched, the R32 reinterpretation changes

`experiments/exp-9-single-channel-swap-vacuous.patch` removes the
over-cautiousness in `tu6_format_list_swaps_are_uniform()`: a single-channel
format has no channel order to argue about with a foreign format; only block
shape must match, because the tile layout is a function of
`util_format_get_blocksize()` (`fd6_layout.c:115`) and nothing more.

The model over 72 classes (`scripts/fmt-list-gate-check.py --exp-9`)
matched the measurement: **exactly 2 classes** flip, and both are
typeless-BGRA8/BGRAX8 under UAV. The cross-order `{B8G8R8A8, R8G8B8A8}`, the
BGR family `{B5G6R5, B5G5R5A1}`, `ZINK_BIND_MUTABLE` without a list and NV12
remain linear — verified by separate lines in the model's output.

What this means in substance:

* **The gain is real.** The vkd3d list for BGRA8-UAV is the most common case
  (a typical texture with a UAV flag), and it gets tiled instead of linear.
  UBWC on it is off anyway, so the gain is exactly in the tiling.
* **The BGRA8 side is untouched.** Fill and readback in the image's format —
  EXACT, `0/262144`. So ordinary SRV/UAV access to such a resource reads
  exactly the same as on linear.
* **Only the second view changes.** The `R32_UINT` view hands out the word with
  byte 0 and byte 2 swapped. For a D3D11 typed UAV load this is exactly the
  case for which vkd3d adds `R32` to the list.

### 21.4 exp-10: MUTABLEEN without UBWC — worse than doing nothing

`experiments/exp-10-is-mutable-without-ubwc.patch` sets
`is_mutable = true` inside the `else if (!mutable_ubwc_fc)` branch
(`tu_image.cc:558`), where UBWC is already off — i.e. exactly that combination
`tiled + UBWC off + MUTABLEEN` that remained unmeasured in §14.

The measurement gives an all-around "no":

* The `R32_UINT` view **didn't change by a single byte** relative to the run
  without MUTABLEEN — i.e. the experiment didn't achieve its goal.
* The BGRA8 side meanwhile **broke**: `261119/262144` words MISMATCH,
  `pat=0x5a5a0000 got=0x5a00005a`, `zeros=0`, "untouched (0xCD)=0" — i.e. this
  is not a lost write but a genuine permutation.

The character of the breakage is the same as in §14 for probes T and S: **the
swap is applied once instead of twice.** The write goes through the layout,
where `fd6_pipe2swap()` under tiling forcibly returns WZYX to all formats
except `is_mutable` (`fd6_format_table.c:396-398`), while the read goes through
the view, where at `is_mutable` the format returns its own WXYZ
(`fd6_texture_format`, `fd6_view.cc:252`). Previously this wasn't visible
because probes H/T/S/S2/S3 used lists with the **same** swap: the asymmetry
doesn't show up there. The vkd3d list is the first measured list where the
swaps diverge, and on it the breakage is visible immediately.

**The gap in `is_mutable` measurements is closed:** the problem is not in the
missing UBWC/sparse combination but in `is_mutable` itself on gen2 with
diverging swaps. `is_mutable = true` is set in exactly one branch
(`tu_image.cc:591`), and the unreachability of that branch on gen2 was a
consequence, not a cause.

In the gate model this is reflected by a flag rather than by substituting the
layout: `tu_image_init(..., exp_10=True)` gives `tiled, no-UBWC, MUTABLEEN` —
the tiling doesn't change, only the contents do. That's exactly what the
measurement shows, and precisely why the flag is useless as an optimization.

### 21.5 What remains unclosed

* **1024 words out of 262144** in the tiled run matched the pattern even though
  the other 261120 diverged. The mismatches run from (0,0) to (511,511), i.e.
  this tail lies in the middle and doesn't follow the geometry of the
  64-texel chunks. exp-10 gives **exactly the same number**, so this is
  not an artifact of a single run; the cause is not established. The share is
  0.39%, it doesn't affect the verdict.
* **A view in the `B8G8R8A8_UNORM` format through `usampler2D` is unreadable** —
  this is invalid usage (the shader expects an integer format); the probe gave
  the same garbage `0xffcdcd89` in both modes. Therefore probe V measures only
  the R32 channel, and the BGRA8 side is measured by probe U. For completeness
  a separate float shader (`sampler2D` + `vec4`) would be needed, but it changes
  nothing: the BGRA8 side is already covered by probe U through the image's
  format.
* **Whether vkd3d sees this difference in practice.** For the gain it matters
  that the `R32` members of the list are auxiliary: vkd3d adds them to allow
  typed UAV load and usually creates a view in the resource's own format. This
  couldn't be verified against vkd3d's code — there are no vkd3d-proton sources
  on the machine.
* **Ship status of 0009.** The tiling gain is measured, the BGRA8 side is
  untouched, but the change in visibility through the `R32` view is real, not an
  artifact. Formally exp-9 belongs to `experiments/` until it is decided whether
  D3D11 code accesses the 32-bit raw reinterpretation of BGRA8. This is the
  same class of caveat as in §16 for the cross-order, and its risk is lower (it
  affects only raw-32 reads, not the picture).

### 21.6 What remains after 0010: the 38 "tiled, no-UBWC" classes analyzed by blockers

After 0010 no class of `tests/vkd3d-fmt-classes.h` goes into linear. What
remains is the single unused lever — removing UBWC on the 38 classes that live
in the `tiled, no-UBWC` mode. The question is whether this is blocked by the
gate (then the rule can be edited) or by the format itself (then there is
nothing to do).

#### 21.6.1 The model didn't distinguish these two cases

`fd6_ubwc_compat_mode()` returns `UNKNOWN_COMPAT` for a format that has no row
in the blob table, and `tu6_mutable_format_list_ubwc_compatible()` silently
returns `false` on that. `UNKNOWN` by itself means two different things, and
the model conflated them: either there is no compatibility in hardware, or the
compatibility exists and the table just doesn't know about it. Analyzing this
by enumeration is pointless — a measurement is needed.

Additionally the model didn't account for `has_8bpp_ubwc`: a property from
`freedreno_devices.py` by which on gen2 UBWC for 8-bits-per-block formats is
disabled entirely, regardless of the gate.

#### 21.6.2 Probe CEIL: gate or format

A probe `probe_ubwc_ceiling()` was added — only `memreq`, no fill or read. Three
images with the same list: linear (size reference), the same format **without**
a list (what it can do by itself) and the same with `MUTABLE_FORMAT` (what the
gate decided). It distinguishes exactly what is needed:

| line | meaning |
|---|---|
| `no ceiling` | `nonmut == lin`: the format holds no UBWC under any list |
| `GATE REMOVED` | `nonmut > lin`, but `list < nonmut`: the gate removed what the format can do |
| `gate is not in the way` | the list made nothing worse |

The point of the split is this: at `n = 1` the gate function returns `true`
unconditionally (`tu_formats.cc`, the `viewFormatCount == 1` branch), i.e. a
single format passes the gate entirely. So `nonmut > lin` is a statement about
hardware, and `list < nonmut` is a statement about the rule.

Logs: `results/test-CEIL-stock.log` (system stock from `/usr`, without the
project's patches), `results/test-CEIL-base.log` (working stack 0004–0009), and
`results/test-CEIL-exp0011.log` (the same stack plus 0011). The first two lines
of section 21.6.3 are about stock; the isolated A/B is in 21.6.4.

#### 21.6.3 Stock: the 41 classes without UBWC split into 18 and 23

The model (`--blockers`) and the measurement agreed on the boundary:

| group | size | blocked by | cured by the rule? |
|---|---|---|---|
| BC1–BC7, NV12, P010, P016 | **18** | `ubwc_possible()`: compressed and planar | **no** |
| R8_*, R16_*, R32G32B32_*, R10G10B10A2_*, B8G8R8A8_TYPELESS+UAV | **23** | the gate itself | **yes** |

The first 18 are a false target. `vk_format_is_compressed()` and
`vk_format_get_plane_count() > 1` cut them off before the gate is even
reached, and no edit of `fd6_ubwc_compat_mode()` touches them.

The second 23 are the only ones where an edit makes sense, and within them the
measurement split things that the model reported as one block:

| format | stock | conclusion |
|---|---|---|
| `R8_UNORM`, `R8_UINT` | `nonmut == lin` | **no ceiling.** `has_8bpp_ubwc = false` is a workaround for a text corruption bug (`0f770caa232`, "Disable 8bpp_ubwc on a6xx gen2", corruption in VSCode on a680), not a measured incapability — but it also cannot be re-enabled without reproducing the bug again |
| `R32G32B32_SFLOAT/UINT/SINT` | `nonmut == lin` | **no ceiling.** The rows exist in `fd6_format_table.c` but without a render buffer: `VT_(R32G32B32_UINT/SINT/FLOAT)` — vertex and texture only (`VT_` = `FMT(pipe, fmt, fmt, NONE, swap)`, file header), while `V__` for UNORM/SNORM has no texture at all. There are no rows in the compat table, and UBWC is not visible in the measurements |
| `R16_*` (4 classes) | `nonmut = 0x82000` > `lin = 0x80000`, `list = 0x80000` | **the gate removed what the format can do.** There is a ceiling |
| `R10G10B10A2_TYPELESS` | `nonmut = 0x102000` > `lin = 0x100000`, `list = 0x100000` | **same.** There is a ceiling |

#### 21.6.3a How this looks on the working stack

The analysis above is on stock, where the `_SFLOAT` classes and cross-order
lists are not yet fixed. On the working stack (`0004, 0005, 0006, 0009, 0010,
0012`) of the 72 classes 36 hold UBWC, **0 linear**, and 36 remain in
`tiled, no-UBWC` — before 0012 and 0010 there were 38 of them. The breakdown of
these 36:

| what | size | can it be edited |
|---|---|---|
| BC1–BC7, NV12, P010, P016 | **18** | no: `ubwc_possible()` |
| `R8_TYPELESS`, `R8_UNORM/UINT/SINT/SNORM` | **5** | no: `has_8bpp_ubwc = false` |
| `R32G32B32_TYPELESS/UINT/SINT` | **3** | no: no ceiling |
| `R16_*`, `R10G10B10A2_*` | **8** | **yes: 0011** |
| `B8G8R8A8/B8G8R8X8_TYPELESS+UAV` | **2** | no: two compat classes in one list |

In total **26 of 36 cannot be closed**, 0011 closes 8, and 2 run into the fact
that vkd3d itself puts two different classes into the list (`B8G8R8A8_UNORM`
for typed reading and `R32_*` for typed UAV load) — this is not a defect of the
rule but the structure of the list.

A caveat about method: the first version of this breakdown grouped by name
prefix, and `G16_B16R16_2PLANE_420_UNORM` fell into the `R16_*` group and
`G8_B8R8_2PLANE_420_UNORM` into `R8_*`, because `R16_` and `R8_` occur in them
as substrings. The final numbers above were counted in reverse order, with
`is_compressed()` checked first, and they add up to 72 (36 + 36 + 0).

The whole breakdown is reproducible with one command — `report_stack()` in
`scripts/fmt-list-gate-check.py`, flag `--stack`.

#### 21.6.4 exp-11: rows for R16_* and R10G10B10A2_*

`experiments/exp-11-ubwc-compat-r16-and-10bit.patch` adds two compat
classes to `fd6_ubwc_compat_mode()` that don't exist in the stock table:
`FD6_UBWC_R16_INT` (5 formats) and `FD6_UBWC_A2B10G10R10_INT` (2 formats).

Here the argument doesn't rely on a contradiction inside the source, as 0006
and 0008 did (those leaned on the stock comment "a630 blob allows these, but
not a660"). The support is the measurement of §21.6.3 plus the fact that both
formats are full-fledged on the texture path: in `fd6_format_table.c` there is
`VTC(R16_UNORM, 16_UNORM, WZYX)` (line 72) and
`FMT(R10G10B10A2_UNORM, …, WZYX)` (line 209), both with swap `WZYX`, i.e.
matching. The gap is exactly in the compat table, and the layout argument works
here in full: all variants of the same block size differ only in the
interpretation of the same bits,
`layout->cpp = util_format_get_blocksize()` (`fd6_layout.c:115`) doesn't depend
on the interpretation, and the swap is common to all members of the list — i.e.
the asymmetry that broke 0010 doesn't exist here by construction.

An error in the first version of this analysis, so as not to repeat it: the
absence of formats was searched via `grep A2B10G10R10 fd6_format_table.c`, i.e.
by the spelling of the Vulkan format, while the table uses pipe names
(`R10G10B10A2_*`). The empty result was interpreted as "the format isn't in the
format table either", although it is, lines 209 and 214. You must check by the
spelling the file actually uses.

The measurement is isolated: `results/test-CEIL-base.log` — the same stack with
0004–0009 and without 0011 (built by a `git apply -R` rollback),
`results/test-CEIL-exp0011.log` — the same stack with 0011. Comparing the
system stock with 0011 wouldn't do for this: `results/test-CEIL-stock.log` has
no patch 0004, and the differences in probes B/E/F/G0
(`sparseResidencyImage2D=0` versus `1`) belong to it, not to 0011.

A `diff` of the two isolated logs gives **exactly three changed lines for the
whole log**:

| class | baseline (without 0011) | with 0011 |
|---|---|---|
| `R16_TYPELESS` | GATE REMOVED (`list=0x80000`) | **gate is not in the way (`list=0x82000`)** |
| `R16_UINT` | GATE REMOVED | **gate is not in the way** |
| `R10G10B10A2_TYPELESS` | GATE REMOVED | **gate is not in the way** |
| `R8_*`, `R32G32B32_*` | no ceiling | no ceiling (untouched) |

On the working stack the model (`--stack`) gives 36 classes with UBWC out of
72, and with 0011 — **44**, i.e. 0011 closes 8 classes (5 on `R16` + 3 on
`R10G10B10A2`). The remainder 36 → 28 doesn't change in composition other than
these eight.

The names in the patch had to be pinned down against the compiler, and this is
worth recording because the obvious name is wrong:
`VK_FORMAT_A2B10G10R10_UNORM_PACK32` maps to
`PIPE_FORMAT_R10G10B10A2_UNORM` (`src/vulkan/util/vk_format.c:114`), while
`PIPE_FORMAT_A2B10G10R10_UNORM` never matches the `switch`. The first build of
0011 failed exactly on this.

#### 21.6.5 What 0011 doesn't prove

* **The contents under UBWC on the `R16_TYPELESS` list were not checked.** The
  CEIL probe measures only `memreq`. Removing the gate leads to `UBWC + tiled`,
  and if something besides the interpretation diverges between `R16_UNORM` and
  `R16_UINT` within one list, this would only surface via a byte roundtrip —
  the same one that broke in §14 and §21.4. The risk here is lower (all `R16_*`
  have the same swap, WZYX, so there will be no swap asymmetry), but the check
  hasn't been done.
* **Fast-clear** is not covered, as in exp-8.
* **`R10G10B10A2_SNORM` and `_SINT` were not added to the compat class.** 0011
  takes `_UNORM` and `_UINT` because in `fd6_format_table.c` the tiled entries
  exist exactly for them (`FMT` line 209 and `VTC` line 214); `_SNORM` and
  `_SINT` have `V__` there, i.e. no tiling. A list with them would run into the
  texture format table rather than the gate.
* **The classification of the 18 classes in the model is by the list of header
  lines**, not by measurement. The measurement confirmed the two main groups
  (`R8`, `R32G32B32`) as "no ceiling", but it wasn't run separately for all 18.

### 21.7 R8 and `has_8bpp_ubwc`: there is a memreq ceiling, the contents are corrupted (measurement 2026-10-02)

The question "`has_8bpp_ubwc = false` on gen2 — protection against corruption
or does nothing break?" was settled by a measurement, without rebuilding: the
driver reads `FD_DEV_FEATURES` and overrides any prop
(`fd_dev_info_apply_dbg_options`, `freedreno_dev_info.c:85`),
`has_8bpp_ubwc` is present in the override table (generated
`freedreno_devices.h:159`).

First by the code: on a7xx_gen2 (FD740, `__info23` of the generated
`freedreno_devices.h`) the `has_8bpp_ubwc` field **doesn't exist at all** — the
C initializer zeroes it. An explicit `False` stands only for `a6xx_gen2/3/4`;
the a7xx chain `[a7xx_base, a7xx_gen2]` doesn't inherit from `a6xx_gen2`. So
the "a6xx gen2 bug workaround" was never carried over to a7xx — the flag simply
isn't set.

Then the measurement. `probe_ubwc_ceiling` for R8 on the baseline
(`has_8bpp_ubwc=0`) gives `nonmut == lin`, and this is **not** a statement
about hardware: the probe goes through `tu6_ubwc_possible()`, where
`has_8bpp_ubwc` is checked (`tu_image.cc:353`) **before** the compat gate. The
flag itself was lowering the "ceiling". With
`FD_DEV_FEATURES=has_8bpp_ubwc=1`:

```
base:   CEIL[R8_TYPELESS] lin=0x40000 nonmut=0x40000 list=0x40000 -> no ceiling
+flag:  CEIL[R8_TYPELESS] lin=0x40000 nonmut=0x42000 list=0x40000 -> GATE REMOVED
```

A `diff` of the whole CEIL block — exactly two R8 lines, the other 8 are
identical. Conclusion 1: **a740 hardware can do 8bpp UBWC** (metadata `+0x2000`)
and the only blocker is the flag. The record "no ceiling by measurement" in the
analysis of the 36 classes was a confounded quantity, not a chip property.

Then the contents: `probe_ubwc_class` (fill + readback) for R8 with the flag:

```
U[R8_TYPELESS] lin=0x40000 nonmut=0x42000 list=0x40000
  linear roundtrip:   0/65536 words EXACT
  nonmut roundtrip:   65536/65536 words MISMATCH  got=0x00000000 (all zeros)
  list roundtrip:     0/65536 words EXACT   (but list=lin: the gate removed UBWC, this is a linear test)
```

Conclusion 2: **the only path where UBWC is really enabled (nonmut, 0x42000)
reads pure zeros** — content corruption. "list EXACT" doesn't prove safety:
there the gate removed UBWC and the linear path is being measured. Conclusion 3:
`has_8bpp_ubwc` is **protection against real corruption** (the same class of bug
as on a6xx gen2, commit `0f770caa232`), keep it False.

Practical summary:

* The R8 classes (5) stay closed: not "no ceiling" but **"there is a ceiling,
  the contents are corrupted"** — a negative result on contents.
* `has_8bpp_ubwc=1` must **not** be enabled globally: single R8 images (not only
  mutable lists) would go down the broken UBWC path.
* R8 in `fd6_ubwc_compat_mode()` (in 0011 or separately) must **not** be added:
  mutable R8 lists would get UBWC and corruption.
* A false alarm was caught at the verification stage: "list EXACT" with the gate
  removed looks like success but measures the wrong thing. The rule: a roundtrip
  proves safety only for the mode that actually received UBWC
  (`nonmut/list > lin`).

Logs: `results/test-r8-has8bpp-on.log`, `results/test-r8-has8bpp-off.log`
(full `rp6-vkd3d-sparse-test` run with the added R8 probes, baseline
`drv-base.so`).
