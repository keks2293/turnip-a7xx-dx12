# turnip-patches

Lifting the `sparseResidencyImage2D` gate in Turnip, which kept vkd3d from
getting tiled resources and made feature level 12_0 unreachable on A740
(Adreno gen2).

Device: Retroid Pocket 6 (aarch64), GPU **Adreno 740 (FD740, a7xx gen2)**,
Mesa 26.2.3, stock driver `mesa-vulkan-drivers-26.2.3-1.fc44.armada.aarch64`.

## The gist in two paragraphs

Turnip only exports `sparseResidencyImage2D` when
`info->props.ubwc_all_formats_compatible` is set - i.e. on gen3 (A750+) and
newer. gen2 has no such flag, so the feature is never exported, vkd3d does
not get `VK_IMAGE_TILING_OPTIMAL` and bottoms out at `TIER_1` instead of
`TIER_2`, and `TiledResourcesTier` 12_0 is unreachable. The hardware and the
kernel are fine: VM_BIND and PRR exist on the device and the driver detects
them. The only obstacle is feature exposure.

Three patches lift the gate and close the hole that lifting it opens: on
A740 a sparse resource must not be linear, otherwise it silently reads back
as zeros.

## What's here

| | |
|---|---|
| `docs/analysis.md` | the analysis: symptom, cause, measurements, applicability boundaries, reproduction; `docs/rp6-vkd3d-analysis.md` - the original short analysis (21.09.2026) it grew out of |
| `patches/` | 0001-0012 - the Mesa patches that ship; `PATCHES.md` - the origin of 0001-0003 |
| `experiments/` | `exp-1`-`exp-11` - **not** for commit: reproducible checks of the analysis' conclusions |
| `tests/` | `rp6-vkd3d-sparse-test.c` + shaders; every result was obtained with it |
| `scripts/` | build and runs, including the experiment script and game launch |
| `container/` | toolchain Containerfile (there is no compiler on the device) |
| `image-build/` | copy of the mesa package build recipe for the image: the Mesa version pin, how 0001-0003 get into the RPM |
| `results/` | filtered run logs: stock, patches, both experiments, the game (`results/raw/` - full logs, outside git) |

Patches 0001-0003 are ArmadOS image build patches; they ship here too so
that the stack is complete in one place. How they get into the mesa RPM -
`image-build/build.sh` (a recipe copy, self-contained, nothing required
from any other repository), the version pin - `image-build/BASE.env`, the
origin of each and the external sources - `patches/PATCHES.md`. The build
image has its own copies: they must not be removed from there - the image
build would break, in particular the workaround for A740 GPU faults would
be lost.

## Patches

| Patch | What it does | Verification (stock -> patches) |
|---|---|---|
| **0001** | disables cross-sync between graphics and sparse queues - works around translation-fault storms on A740 | image patch, source and notes - `patches/PATCHES.md` |
| **0002** | adds the A830 chip-id (ported from ROCKNIX) | image patch, same place |
| **0003** | disables bindless UBO const lowering in ir3 (ROCKNIX, offsets for Mesa 26.2.0) | image patch, same place |
| **0004** | lifts the gate; adds a create refusal for the sparse+MUTABLE->linear hole | `sparseResidencyImage2D` `0 -> 1`; hole creates `SUCCESS -> FEATURE_NOT_PRESENT` |
| **0005** | implements the upstream TODO: uniform-swap lists stay tiled | probe S: `FAIL` (all zeros) -> `PASS`, both views exact |
| **0006** | the B8G8R8A8 family is unified onto an INT-compatible type | probe T: `memreq 0x100000 -> 0x102000` (UBWC metadata), roundtrip 0/262144 |
| **0009** | NV12 + MUTABLE: only UBWC is dropped, tiling is preserved (removes the only path into the 0004 rejection - no vkd3d patch needed) | probe N: `FEATURE_NOT_PRESENT -> SUCCESS` |
| **0010** | a list where a single-channel format disagrees with a multi-channel one stays tiled: tiling of the vkd3d typeless-BGRA8 list under UAV | probe V: list `linear -> tiled`; BGRA8 side `roundtrip EXACT 0/262144`; the R32 view diverges from linear in `261120/262144` |
| **0011** | rows for `R16_*` and `R10G10B10A2_*` in `fd6_ubwc_compat_mode()`: the stock table has none, and any vkd3d list with R16 silently loses UBWC | model: 36 -> 44 UBWC classes out of 72; probe CEIL: a ceiling exists (`0x82000`/`0x102000`), the gate removes it. Caveat below |
| **0012** | `_SFLOAT` members in the integer UBWC compat classes of `fd6_ubwc_compat_mode()`: a vkd3d list mixing `_SFLOAT` and integers no longer loses UBWC wholesale | probe U: for five TYPELESS classes the list kept UBWC (`list=0x102000`); an isolated A/B on the stack - exactly 5 log lines. Caveat below |

Relations between the patches (details in each header, the `Relations:`
line): 0010 requires 0005 (which adds
`tu6_format_list_swaps_are_uniform()`); 0009 builds on the 0004 refusal;
0006, 0011 and 0012 edit one compat table `fd6_ubwc_compat_mode()` but
different rows of it, and apply together without conflicts (verified by
laying the whole stack 0005-0012 onto a clean HEAD). 0007 and 0008 are
independent of everything.

0004 is needed for gen2 only (FD730/735/740, FD740v3, A32, X1-45, X1-85):
on gen3 and a8xx it is unreachable, the hole does not occur there.

0009 is a general fix to `tu_image.cc`, not a per-chip quirk: it removes
the demotion of NV12 to linear, which is unnecessary because only the
compression changes, not the layout. An upstream candidate.

0010 applies on top of 0005 (the one that adds
`tu6_format_list_swaps_are_uniform()`) and drops its requirement that
swaps match for single-channel formats. On tiling, no swap for the view
exists: `fd6_pipe2swap()` returns WZYX to every format except
`is_mutable` (`fd6_format_table.c:396`), so the `WXYZ` vs `WZYX`
difference has no representation there and cannot be the cause of
demotion. The block shape is checked for everyone because the tile
layout depends only on the block size (`fd6_layout.c:115`). The
cross-order `{BGRA8, RGBA8}` and NV12 are not affected: there the
disagreement really is about channel order.

**Caveat for 0010, measured on the 740.** The patch changes visibility
through a second view: the `R32_UINT` view returns a word with bytes 0
and 2 swapped, `261120/262144` words differ from the linear path. The
BGRA8 side (fill and readback with the image format) is untouched -
`EXACT 0/262144`. Under D3D12 the divergence is safe: a typed UAV load
of BGRA8 returns `float4` and is read through a view of the resource's
own format, and a resource with `ALLOW_UNORDERED_ACCESS` stays typed
BGRA8, so a raw 32-bit read through an `R32` view is inexpressible in
D3D12. This could not be verified against vkd3d sources - they are not
on the machine - so the caveat rests on the D3D12 specification and on
the measurement. Analysis and logs - `docs/analysis.md` §21.

**0005 and 0006 change nothing for real consumers.** Analysis
`docs/analysis.md` §16.2 (enumeration - `scripts/fmt-list-gate-check.py`):
of the 72 classes in `tests/vkd3d-fmt-classes.h`, exactly two go to
linear on stock - `B8G8R8A8_TYPELESS+UAV` and `B8G8R8X8_TYPELESS+UAV`,
i.e. the very list vkd3d builds for typeless-BGRA8 with
`ALLOW_UNORDERED_ACCESS`. Of the 11 sRGB pairs Zink reaches the
uniform-swap classification with five, but GL hands Zink only
`B8G8R8A8_UNORM` from them, and its pair passes
`tu6_mutable_format_list_ubwc_compatible()` even on stock.

Neither patch reaches those two classes, for different reasons. 0005
does not help because the swap divergence there is not between two
multi-channel formats but between `B8G8R8A8_UNORM` (WXYZ) and
single-channel `R32*` (WZYX) - exactly the case that
`tu6_format_list_swaps_are_uniform()` still rejects. 0006 does not help
because the gate falls not on `B8G8R8A8` vs `B8G8R8A8_UINT` but on
entirely different compat classes: `B8G8R8A8` vs `R32_INT`. Those two
classes are closed by 0010, measured in §21.3, which also shows that the
change affects only raw 32-bit reads through the second view. 0006 is
not part of the ArmadOS working stack
(`0004, 0005, 0007, 0008, 0009, 0010`).

With 0010 on gen2 **no class of `tests/vkd3d-fmt-classes.h` goes to
linear**: linear demotion of vkd3d format lists is removed entirely.
What remains is dropping UBWC, and that is the only unused lever. On
the working stack (`0004, 0005, 0006, 0009, 0010, 0011, 0012`) out of
72 classes, 44 hold UBWC, 0 are linear, 28 live in `tiled, no-UBWC`
mode (before 0011 there were 36; before 0012 and 0010 - 38).

§21.6 dissected those 36 and showed that **26 of them cannot be closed
by anything** - a negative result, not unfinished work. The measurement
(probe CEIL, `results/test-CEIL-*.log`) separates the gate from the
format itself: with `viewFormatCount == 1` the gate returns `true`
unconditionally, so the single image bypasses it entirely, and `memreq`
of a single format vs linear is a statement about the hardware, not
about the rule.

The model reproduces the layout: `scripts/fmt-list-gate-check.py
--stack` gives 36/0/36 (0004-0010 and 0012 - `_SFLOAT` was always
counted in the stack in the model, now it is a ship patch), with
`--exp-0011` (patch 0011) - 44/0/28.

| what | size | can it be fixed |
|---|---|---|
| BC1-BC7, NV12, P010, P016 | 18 | **no.** `vk_format_is_compressed()` and the plane count cut them off before the gate even gets to think |
| `R8_UNORM/UINT/SINT/SNORM`, `R8_TYPELESS` | 5 | **no.** `has_8bpp_ubwc = false` on gen2. A memreq ceiling exists (`0x40000 -> 0x42000` with `FD_DEV_FEATURES=has_8bpp_ubwc=1`), but the contents under UBWC are corrupted: nonmut roundtrip `65536/65536`, all zeros (`results/test-r8-has8bpp-on.log`). The flag protects against corruption (the same bug as on a6xx gen2), keep it false |
| `R32G32B32_TYPELESS/UINT/SINT` | 3 | **no.** No ceiling in the measurements |
| `R16_*`, `R10G10B10A2_*` | 8 | **yes.** A ceiling exists and the gate removes it - `patches/0011-...` |
| `B8G8R8A8/B8G8R8X8_TYPELESS+UAV` | 2 | **no.** The list holds two different compat classes - `B8G8R8A8_UNORM` and `R32` |

Caveat for 0011 of its own: the change also affects `R16` views, and the
contents under UBWC were never checked with a byte roundtrip - probe
CEIL measures only `memreq`.

Caveat for 0012 of its own: fast-clear is not covered by a byte
roundtrip - the driver does not compare clear metadata against the
format interpretation, and a wrong value will only show where an
application does a fast-clear through a view with float interpretation.
No probe closes this; the risk is recorded in the patch header.

Patches **0007** (`TU_FORCE_PROPS`, diagnostics only) and **0008**
(`gen2`: `has_generic_clear` + `r8g8_faulty_fast_clear_quirk`) live in
`patches/` but are not in the README table - by definition 0007 must
not work without environment variables, 0008 is switched by the device
table.

The numbering of `experiments/` and `patches/` **cannot collide**: the
probes are `exp-N`, the patches are `000N`. The cross-order tiled+UBWC
probe is `experiments/exp-6-cross-order-list-tiled-ubwc.patch`
(analysis - `docs/analysis.md`, §16, §16.1 and §16.2: neither vkd3d nor
Zink has any consumers of such lists), while the patch
`0006-turnip-b8g8r8a8-int-compat-type.patch` adds `FD6_UBWC_B8G8R8A8_INT`.
Which experiment went into which patch and which of those is already in
mesa - the "Experiment -> patch -> mesa" table in the section below.

### About the build image

Rebuild the driver with the container
`localhost/mesa-build-fedora44-wsi-glslang`. The image
`mesa-build-fedora44-full` is the result of a `podman commit` (section
15 of `docs/analysis.md` -> now 19), and it lacks the WSI-devel
packages without which ninja fails at link time. Details and contents
verification - section 19.

## Patch-to-mesa commit mapping

The `0001-00XX` numbering is kept only here, in turnip-patches: it is
the internal queue of the stack and experiments, referenced by `docs/`,
`results/`, `experiments/` and the patch headers. In
[keks2293/mesa](https://github.com/keks2293/mesa) commits carry no
numbering and follow the usual mesa style: subsystem prefix + imperative
description, without `000N` and internal stage numbers.

Branches in mesa:

- **`fix-auth`** - author keks2293, the original (Russian) messages;
- **`fix-auth-en`** - the same tree, messages translated into English
  with the context preserved, in mesa style.

This repository follows the same scheme: the `fix-auth` branch carries
the original Russian messages, `fix-auth-en` the same 39 commits with
all messages translated into English (semantically, keeping the
`0001-00XX` numbering, tables, file references and artifacts).

| Patch / stage | Area | mesa commits (`fix-auth` -> `fix-auth-en`) | mesa-style subject (EN) |
|---|---|---|---|
| 0001-0003 | image build | `3bb596f` -> `f16b56c` | `armada: stock image patches 0001-0003` (unchanged) |
| 0004 | `turnip/sparse` | `4ca598f` -> `9ad3ab7` | `turnip: sparse: relax sparse residency gating and refine linear/sparse image creation checks` |
| dgc M2.0 | `freedreno/dgc` | `e986710` -> `64bfdbc` | `freedreno/dgc: prototype GPU-written PM4 via CP_INDIRECT_BUFFER` |
| dgc M2.1 | `freedreno/dgc` | `1296148` -> `c3db727` | `freedreno/dgc: implement VK_EXT_device_generated_commands (features, properties, entry points)` |
| dgc M2.2 | `freedreno/dgc` | `a5c6af7` -> `eb0f1fe` | `freedreno/dgc: add v1-scope validation to reject tokens outside the recorded range` |
| dgc M2.3 | `freedreno/dgc` | `565d03f` -> `6f576ec` | `freedreno/dgc: add GPU-side translator for command sequences to PM4 (WIP: stream path verified, draw submission not yet functional)` |

The DGC commits (stages M2.0-M2.3) are a separate development line in
mesa and do not correspond to patches `0001-00XX`; the internal stage
numbers survive only in the original messages of the `fix-auth` branch.
Patches `0005`, `0006`, `0009-0012` have not been moved to mesa yet -
the mapping grows as things are ported.

### Experiment -> patch -> mesa

File names are shortened (all live in `experiments/`); what each showed
and its logs - `experiments/README.md` and `docs/analysis.md`. Experiments
never go to mesa themselves - only their results do, as `patches/000N`.

| Experiment | What it showed | -> Patch | mesa commit |
|---|---|---|---|
| `exp-1-ubwc-all-formats-compatible-a7xx-gen2` | negative result: gen2 cannot reinterpret UBWC on a format change | — | — |
| `exp-2-force-is-mutable` | isolation: MUTABLEEN itself breaks reads | — | — |
| `exp-3-no-sparse-create-refusal` | game run: 0004's create refusal and the "GPU does not meet the minimum requirements" message (§9) | — (checks `0004`) | — |
| `exp-4-force-wzyx-when-mutable` | the MUTABLEEN corruption lies entirely in swap | — | — |
| `exp-5-ubwc-on-without-mutableen` | the capability is not there, not "the bit is harmful" | — | — |
| `exp-6-cross-order-list-tiled-ubwc` | mechanically survives, but neither vkd3d nor Zink builds such lists (§16) -> rollback | — | — |
| `exp-7-nv12-keep-tiling` | N2 (NV12 + MUTABLE + SPARSE): `FEATURE_NOT_PRESENT` -> `SUCCESS` (§18) | `0009` | not moved yet |
| `exp-8-sfloat-int-ubwc-compat` | 5 TYPELESS classes gain UBWC, 31 -> 36 classes (§20.5) | `0012` | not moved yet |
| `exp-9-single-channel-swap-vacuous` | exactly 2 of 72 classes get tiling (§21.3) | `0010` | not moved yet |
| `exp-10-is-mutable-without-ubwc` | is_mutable alone does not break anything (§21.4) | — | — |
| `exp-11-ubwc-compat-r16-and-10bit` | 8 of 72 classes with UBWC, the gate is what lifts it (§21.6) | `0011` | not moved yet |

## What was verified by measurement

- **UBWC is enabled on A740** for regular images: probe G gives
  `0x102000` vs `0x100000` for the linear control. "UBWC is disabled"
  refers to mutable lists without a common compat type and to the
  sparse path, not to the device.
- **sparse + UBWC is a structural blocker, not a hardware limit.**
  Metadata lives in a separate `fdl_layout` plane, and
  `vkGetImageSparseMemoryRequirements` and `fdl_sparse_miptail_*`
  compute requirements from the data plane only, so no reported range
  covers the metadata.
- **`MUTABLEEN` does not work on gen2** - the main result, closing the
  open question. Enabling the flag breaks reads, and isolating it with
  an experiment shows that the bit itself breaks, not "something else
  turned on": with forced MUTABLEEN `T-tr` gives 261120/262144
  mismatches instead of 0, with the format pair unchanged. The data is
  intact, the channel order is broken. Details and tables -
  `docs/analysis.md`.
- **8-bit integer sampling works correctly on A740** (probe R: a real
  `R8G8B8A8_UINT` + usampler - EXACT). The apparent 8888-INT read
  anomaly is the specified "poison" value for an UNORM image with a
  usampler, i.e. a test artifact, not a bug.

## Quick start

A short list; the podman invocations and the step order are in the
"Build and tests" section below.

```sh
scripts/build-turnip.sh            # Mesa + patches, turnip only, KMD=msm
scripts/rebuild-turnip-msm.sh      # incremental, if only flags changed
scripts/build-test.sh              # build the test (in the container)
scripts/run-test.sh ""                       results/test-stock.log
scripts/run-test.sh out/freedreno_icd.json    results/test-patched.log
scripts/run-experiment-mutableen.sh          # re-check the MUTABLEEN conclusion
scripts/run-game.sh stock 300                # the game (RE4, DX12) on the packaged driver
scripts/run-game.sh patched 120              # the game on the patched one (VK_DRIVER_FILES)
```

Launching the game requires `CWD` = the game folder: otherwise RE4
writes an empty `local_config.ini` to the wrong place and you get a
black screen. Details - section 8 of the analysis.

The driver is handed to the loader through `VK_DRIVER_FILES` and its
own ICD manifest - `/usr` on the device must not be touched, and
`sudo` requires a password. Section 7 of the analysis describes this
in detail.

**Do not run the build and the game at the same time.** The device
memory (7.3 GB) is shared with the GPU; the game together with
`ninja -j$(nproc)` pushes the system into OOM - the cursor freezes and
a reboot is needed. `scripts/build-turnip.sh` checks for a running
Wine before starting.

Build images: `localhost/mesa-build-fedora44-wsi-glslang` (built from
the two Containerfiles in `container/`). `-Dplatforms=` in
`scripts/build-turnip.sh` affects more than the test: without the WSI
extensions the driver fails `vkCreateInstance` under proton, see
section 9 of the analysis.

## Build and tests

There is no compiler on the device: both the driver and the test are
built in podman. Both builds expect the same mounts - the Mesa tree at
`/src`, the `build/` directory at `/out`, the repository at `/repo`:

```sh
REPO=/var/home/armada/opencode/turnip-a7xx-dx12
WORK=/var/home/armada/opencode/build
IMG=localhost/mesa-build-fedora44-wsi-glslang
```

The `build/ccache` directory lives inside the image container, so to
share the cache between runs add `-v $HOME/.cache/ccache:/ccache`.

### Driver

Sources with the patches applied - `$WORK/mesa-rp6`. A new patch is
applied and reverted with `git apply`; do not use `git checkout` - it
destroys the uncommitted stack (0005-0010 were already lost that way).

```sh
cd $WORK/mesa-rp6
git apply $REPO/patches/0011-turnip-ubwc-compat-r16-and-10bit.patch   # example

# full rebuild (rm -rf build + meson setup, minutes):
podman run --rm -v "$PWD":/src:Z -v "$WORK":/out:Z -v "$REPO":/repo:Z \
    $IMG bash /repo/scripts/build-turnip.sh

# incremental (same code change, no setup from scratch):
podman run --rm -v "$PWD":/src:Z -v "$WORK":/out:Z -v "$REPO":/repo:Z \
    $IMG bash /repo/scripts/rebuild-turnip-msm.sh

# revert:
git apply -R $REPO/patches/0011-turnip-ubwc-compat-r16-and-10bit.patch
```

The output is `$WORK/out/libvulkan_freedreno.so` and
`$WORK/out/freedreno_icd.json` - this is what gets handed to the
loader via `VK_DRIVER_FILES`. Look for the result in `out/`, not in
the root of `build/`: the manifest points into `out/`, and a stale
copy looks like "the patches were not applied" (analysis, §6).

### Test

```sh
# build (the script installs missing devel packages itself; dnf access required):
podman run --rm -v "$REPO/tests":/work:Z -v "$WORK":/out:Z -v "$REPO":/repo:Z \
    $IMG bash /repo/scripts/build-test.sh

# run on the device - the video/render groups are required and the game must be closed:
source $REPO/scripts/env.sh
$REPO/scripts/run-test.sh ""                $REPO/results/test-stock.log
$REPO/scripts/run-test.sh "$RP6_TURNIP_ICD" $REPO/results/test-patched.log
```

The run takes seconds and the log is written in full - there is no
point filtering probes. Ready-made logs live in `results/`; if a log
is missing the build was never run, see `build-test.sh`.

### Hardware-free verification

The gate model computes the layout of all 72 vkd3d classes straight
from the sources:

```sh
cd $REPO
python3 scripts/fmt-list-gate-check.py --stack            # 0004-0010 + 0012: 36/0/36
python3 scripts/fmt-list-gate-check.py --stack --exp-0011  # + 0011: 44/0/28
```

### Experiments

Any patch from `experiments/` is checked with an "apply -> rebuild ->
run -> revert" loop; the expected result of each is in
`experiments/README.md`:

```sh
cd $WORK/mesa-rp6
git apply $REPO/experiments/<patch>.patch
# rebuild the driver (see above), then run the test (see above)
git apply -R $REPO/experiments/<patch>.patch
```

### Games

```sh
$REPO/scripts/run-game.sh patched 300   # RE4 (DX12), driver from build/out
$REPO/scripts/run-game.sh stock 300     # RE4 on the packaged driver
$REPO/scripts/run-wtno.sh 180           # WNO (OpenGL, turnip not involved)
```

## Status

**12_0 achieved on a live application.** The gate lift is confirmed
twice: by measurement on our own test (`sparseResidencyImage2D=1`) and
by the log of the running game - the device is created at 12_0, the
`0xc000 is not supported` rejection is gone (only `0xc100` remains -
that is 12_1: `ROVsSupported` requires
`VK_EXT_fragment_shader_interlock`, not implemented in Turnip).
Analysis - section 9 of the doc, log - `results/game-patched.log`.

The side effect for which the gate was lifted is now visible in
action: the game on 12_0 does not reach a window. Within 200 s the
swapchain never comes up, and on screen is a window reading "your GPU
was not supported". That is the game's own rejection, not proton's:
the log is clean, not a single `err:` and not a single `Exception`,
and the game reaches `d3d12_device_CheckFeatureSupport` and raises the
shader cache - i.e. the 12_0 path works, what breaks is a capability
check.

**The cause of the rejection was found - it is not the patches'
gate.** The game requires SM 6.6, which is granted by condition 6.2
in vkd3d: `shaderDenormPreserveFloat32`, which Turnip takes from the
driconf option `tu_enable_softfloat32`, enabled in
`00-turnip-defaults.conf` **only for the `vkd3d` engine**. The path to
driconf is baked into the binary from the build `prefix`, and our
build had `prefix=/out/usr` - the driver looked for
`/out/usr/share/drirc.d`, which does not exist on the device. The
packaged driver is built with `prefix=/usr` and reads
`/usr/share/drirc.d`. `vulkaninfo` did not show this: its engine name
is not `vkd3d`, so both drivers report `Preserve32 = false`.

**The game works.** Verified by runs: `VKD3D_SHADER_MODEL=6_6` on the
patched driver (`results/game-patched-sm66.log`); stock with
`VKD3D_FEATURE_LEVEL=12_0` (`results/game-stock-flforce.log`); and a
clean run on the rebuilt driver - neither `DRIRC_CONFIGDIR` nor
`~/.drirc`, SM 6.6 comes up by itself and the force never applies
(`results/game-patched-clean.log`).

Analysis - section 10 of the doc. The build fix (`--prefix=/usr` in
`scripts/build-turnip.sh`) removes the cause: the driver reads the
same `/usr/share/drirc.d` as the packaged one, and SM 6.6 comes up by
itself on any launch - from Heroic, from a console, from our scripts.
The interim workarounds (`DRIRC_CONFIGDIR`, `~/.drirc`) were removed
rather than kept "just in case": they would mask the cause, and the
next debugging session would look for it in the wrong place.

**Performance.** 13 fps in the game is a GPU limit, not vsync and not
CPU: `gpu_load` 85-99%, core 680 MHz, 68-71 °C without throttling,
55-70 fps in the menu. Measured via MangoHud (`HUD=1` - overlay,
`HUDLOG=90` - also CSV), analysis in section 11. Still open: how much
of those 13 fps `softfloat32` eats - that needs RE4's built-in
benchmark, not a manual scene.

**The DX11 probe failed:** the game does not read
`[Render] Capability` from `local_config.ini` and overwrites it back
(section 11.2). The command-line key was not tried. The value of such
a check is limited: DX11 goes through DXVK and does not touch our
patches - they must be checked on a different DX12 game.

Still unverified: vkd3d's reaction to `VK_ERROR_FEATURE_NOT_PRESENT`
for mutable+sparse without a format list - three probes (E, F, B)
that used to be created successfully on stock are now rejected by
design of 0004, while vkd3d sets `MUTABLE_FORMAT_BIT` broadly and not
always together with a list.
