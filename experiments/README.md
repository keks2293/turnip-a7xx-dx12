# Experiments (NOT FOR COMMIT)

The patches from `patches/` ship; these do not. They exist so that the
conclusions in `docs/analysis.md` can be re-checked rather than taken on faith.

| Patch | What it checks | Result |
|---|---|---|
| `0001-experiment-ubwc-all-formats-compatible-a7xx-gen2.patch` | whether gen2 can reinterpret UBWC on a format change (the `MUTABLEEN` bit) | no: reading through the R32_UINT view falls apart, `H` memreq 0x100000 → 0x102000 |
| `0002-experiment-force-is-mutable.patch` | isolation: is it MUTABLEEN itself that breaks, or "something else got switched on" | MUTABLEEN itself breaks: with the format pair unchanged, `T-tr` 0/262144 → 261120/262144 |
| `0003-experiment-no-sparse-create-refusal.patch` | whether the 0004 create refusal removes what makes the game on 12_0 print "GPU does not meet the minimum requirements" | run on the game, analysis in `docs/analysis.md`, section 9 |
| `0004-experiment-force-wzyx-when-mutable.patch` | rolling the `fd6_format_table.c:396` hack out to the mutable branch: keep `WZYX` also when `is_mutable`. Requires 0002 — without it the T lists have no `is_mutable` | **the MUTABLEEN corruption lies entirely in swap**: `T-tr` 261120/262144 → **0/262144**, `S-tr` 16384/16384 → **0/16384** |
| `0005-experiment-ubwc-on-without-mutableen.patch` | UBWC enabled for an incompatible list while `is_mutable` (i.e. `MUTABLEEN`) is set to 0 — "the bit is harmful" or "the capability isn't there"? | **the capability isn't there**: `H d1'` gives the same garbage byte for byte (`0x079d685e…`) as with the bit |
| `experiment-cross-order-list-tiled-ubwc.patch` | whether tiled+UBWC same-shape keeps a cross-order list `{BGRA8, RGBA8}` (probe S3 in the test): mechanical robustness and cross-view semantics | mechanically yes (`S3-tr` 0/262144, base EXACT, cross-view deterministic), but the behavior changes: identity → swapped (the view's channel order is ignored) vs stock/linear; plus a sparse query/create desync (`E`/`S2` SUCCESS while `FORMAT_NOT_SUPPORTED`) → **rollback**, analysis — `docs/analysis.md`, section 16 |
| `0007-experiment-nv12-keep-tiling.patch` | NV12 + MUTABLE: drop only `force_linear_tile`, keeping the UBWC removal | **yes**, and it was moved to `patches/0009-turnip-nv12-mutable-keep-tiling.patch`: N2 (NV12 + MUTABLE + SPARSE) on 0004–0008 gives `FEATURE_NOT_PRESENT`, with 0009 — `SUCCESS`. Analysis — `docs/analysis.md`, section 18. Caveat: layout identity does not prove the sampling path |
| `0008-experiment-sfloat-int-ubwc-compat.patch` | adding `_SFLOAT` members to the integer UBWC compat classes (`freedreno_ubwc.h`) | **yes, by the gate**: 5 TYPELESS classes get `list=0x102000` (UBWC), roundtrip EXACT, from 31 to 36 classes with UBWC. Fast-clear is not covered by the byte roundtrip — caveat. **Moved to `patches/0012-turnip-ubwc-compat-sfloat-int.patch`** (04.10.2026: an isolated A/B on the 0005–0010 stack changes exactly 5 lines of probe U; the fast-clear caveat is kept in the patch header). Analysis — `docs/analysis.md`, section 20.5 |
| `0009-experiment-single-channel-swap-vacuous.patch` | a single-channel format (`R32_UINT` and the like) must not push the list into linear: it has no channel order | **yes, exactly 2 classes out of 72** get tiling (`B8G8R8A8/B8G8R8X8_TYPELESS+UAV`); the BGRA8 side is EXACT `0/262144`. What changes is visibility through the `R32_UINT` view: bytes 0 and 2 are swapped, `261120/262144` words disagree with linear. Cross-order and NV12 are unaffected. **Moved to `patches/0010-turnip-single-channel-swap-tiling.patch`**, the caveat about the `R32` view — in `README.md`. Analysis — `docs/analysis.md`, section 21.3 |
| `0010-experiment-is-mutable-without-ubwc.patch` | isolation: does `is_mutable` (MUTABLEEN) break things by itself, without UBWC and without sparse — the combination from §14 had remained unmeasured | **no, the rejection is alarming**: the `R32_UINT` view did not change by a single byte, but the BGRA8 side broke — `261119/262144` MISMATCH. The breakage is swap's exchange applied once instead of twice (write through the layout with forced WZYX, read through the view with format WXYZ). The hole in the `is_mutable` measurements is closed. Analysis — `docs/analysis.md`, section 21.4 |
| `0011-experiment-ubwc-compat-r16-and-10bit.patch` | rows for single-channel 16-bit and packed 10-bit in `fd6_ubwc_compat_mode()`: the stock table has no such formats, and any vkd3d list with `R16` or `R10G10B10A2` silently loses UBWC | **yes, by the gate, and this is not over-caution but a gap in the table**: the CEIL probe showed that a lone `R16_UNORM` and `R10G10B10A2_UNORM` hold up without a UBWC list (`0x82000`/`0x102000` vs the linear `0x80000`/`0x100000`), i.e. there is a ceiling and the gate is what lifts it. An isolated A/B (same stack, `git apply -R`) changes exactly 3 log lines: `R16_TYPELESS`, `R16_UINT`, `R10G10B10A2_TYPELESS` go from `GATE REMOVED` to `gate is not in the way`. The model counts 8 classes out of 72 (with 0012). `R8_*` and `R32G32B32_*` are unaffected — they have no ceiling at all. The contents under UBWC and fast-clear were not verified. **Moved to `patches/0011-turnip-ubwc-compat-r16-and-10bit.patch`** (the header no longer says NOT FOR COMMIT; the caveat about unverified content — in `README.md`). Analysis — `docs/analysis.md`, section 21.6 |

The numbering of `experiments/` and `patches/` is independent, so
0004/0005/0007/0008/0009 appear in both directories. The identifiers for the
gate model are by meaning, not by patch number: `--exp-0009` and `--exp-0010` in
`scripts/fmt-list-gate-check.py` refer to this very directory.

All of them apply on a tree with patches 0004–0006, each one individually, and
are reverted with `git apply -R <patch>`. `git checkout` for reverting **is not
usable**: uncommitted 0005/0006 sit in `tu_image.cc`, a checkout would destroy
them. 0002 and 0005 are narrowed down to their own hunk, so apply/-R work on
top of them. The second experiment exists because the first changed two
variables at once (the MUTABLEEN bit and the list composition) and on its own
gave no conclusion about the cause.

Run: `scripts/run-experiment-mutableen.sh` (the first one). For the rest:

```sh
cd $WORK/mesa-rp6
git apply experiments/0002-experiment-force-is-mutable.patch
# rebuild the driver, run the test, T-tr 261120/262144 expected
git apply -R experiments/0002-experiment-force-is-mutable.patch
```

0004 is applied on top of 0002 (otherwise swap is `WZYX` anyway, the
difference is zero), 0005 — on its own. The A/B of 0002 vs 0002+0004 was done
on one test binary: `results/test-expD-control.log` and `results/test-expD.log`;
0005 — `results/test-expE.log`.

The third one is run on the game, not on the test, and requires rebuilding the
driver, so the order is: game closed, patch applied, driver rebuilt,
`scripts/run-game.sh patched 200`, then `git checkout`. Do not run the build
and the game at the same time — device memory is shared with the GPU.

Logs are in `results/test-mutableen.log` and `results/test-expC.log`.
