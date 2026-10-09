# RP6: why DX12 games don't reach feature level 12_0

**Device:** Retroid Pocket 6 — QCS8550, Adreno **A740** (gen2, not A750)
**Image:** Armada main — Mesa 26.2.3, kernel 7.2.6, CachyOS Proton 11
**Measurements:** stock Mesa 26.2.2, 20–21.09.2026

## 1. Symptom

Under Proton 11, games in DX12 do not rise above D3D12 feature level 11_1: vkd3d
does not set `TiledResourcesTier` (it stays `NOT_SUPPORTED`), and the 12_0 gate
is not passed. By the driver's code, it "should get there".

## 2. Cause

Turnip exports the `sparseResidencyImage2D` feature only when two conditions
hold:

- `has_sparse_prr` — PRR is supported by hardware and kernel; it works on RP6;
- `ubwc_all_formats_compatible` — "a UBWC image can be reinterpreted as a
  different format"; the driver advertises this **only for gen3 / A750+**, on
  gen2 it was never verified.

A740 is gen2, so the second condition is always false. The feature is not
exported, vkd3d does not report TILED, and 12_0 becomes unreachable.

There is exactly one obstacle — exposing the feature. Hardware and kernel are
sound: VM_BIND and PRR exist on RP6, the driver detects and uses them. For the
sparse path Turnip disables UBWC altogether, so format casting is impossible
there and the gate is redundant.

Removing the gate is one line: drop `&& ubwc_all_formats_compatible` from the
exposure condition. On gen2 this flag is always false anyway, so the gate was
the only obstacle there, while on gen3 and newer it stays in force.

Turning the flag itself on in `a7xx_gen2` is worse. The flag denotes a concrete
hardware mechanism: the driver sets the `MUTABLEEN` bit in the texture
descriptor, and the hardware with it correctly reinterprets UBWC when the
format changes. Upstream introduced it only for A750+ ("A750+ added a special
flag"), although the bit itself is declared in the register map for a7xx as a
whole, and nobody has tested A740 in this regard in either direction.
**Whether A740 can cast the formats of UBWC images is unknown**, it is not
established that it cannot. The Vulkan spec says nothing about UBWC: it is
proprietary Adreno compression, and in API terms such a claim cannot be
expressed at all.

For removing the gate this question isn't even needed. On the sparse path UBWC
is disabled unconditionally: `ubwc_possible()` returns false for
`SPARSE_RESIDENCY`, because unbound blocks can show fast-clear leftovers. So
the gate checks a capability the sparse path does not use anyway — removing it
is safe regardless of the answer for A740.

But turning the flag on is a bet on the unverified. If the assumption is wrong,
UBWC images with a changing format will go through silently, without a create
error, and that is the non-sparse path — exactly the class where data
corruption was already observed on stock and where 0006 (P6) fixes the
situation by merging B8G8R8A8 on an INT-compatible type.

Beyond that, vkd3d reaches the needed threshold without changes. The
`TiledResourcesTier` gate consists of three steps: exactly one is removed (the
feature exposure), the second passes in full on A740, the third runs into 3D
sparsity, which A740 has none of, and returns `TIER_2` — exactly what feature
level 12_0 requires.

## 3. Fact 1 — 12_0 is reachable without a force

Forcing `VKD3D_FEATURE_LEVEL=12_0` through `game-tweaks.json` raises the level
regardless of the driver, and **games launch and run**. So neither the
application, nor Proton, nor the rendering stack requires anything beyond what
the driver provides — the force merely masks the gate. Hence: 12_0 can be
obtained legitimately, by removing the gate.

The force remains a working crutch, but it does not cure the cause: real tiled
resources are still not provided, and the application gets TILED capabilities
the driver does not actually deliver.

## 4. Fact 2 — the gate can be lifted

The gate is removed, the missing support added, vkd3d patched. All three Mesa
patches are implemented and verified on the device:

| Patch | What it does | Verification |
|---|---|---|
| **0004** (P1+P3) | gate removal + create refusal for the sparse+MUTABLE→linear hole | `sparseResidencyImage2D=1`; create rejected |
| **0005** (P4) | uniform-swap lists behave as tiled+sparse (implementation of upstream's TODO) | create OK, transfer 0/16384, both views exact |
| **0006** (P6) | the B8G8R8A8 family merged on an INT-compatible type | list compatible, UBWC+tiled preserved |

The trivial "it creates, it reads" is not counted as success here: for 0004 a
create refusal is exactly what is needed, otherwise the image is silently
created linear while sparse is enabled, and the data in it gets corrupted.

Separately verified that there is nothing to break: the D3D12 sampling paths
(float and true UINT) are clean, and the apparent 8888-INT read anomaly is the
combination of an UNORM image with a usampler, i.e. the specified "poison"
value — a test artifact, not a driver bug.

## 5. Nuance for upstream

In Mesa `main` the gate is present in two places:

- the exposure of `sparseResidencyImage2D`;
- `vkGetPhysicalDeviceImageFormatProperties2` — the gate was added to the
  format-properties query path (commit "tu: Don't advertise sparse residency
  when the pdev can't do it", Emma Anholt, 03.09.2026).

vkd3d relies precisely on the second: `D3D12_FORMAT_SUPPORT2_TILED` is set
based on the result of querying formats with `SPARSE_RESIDENCY`. Therefore the
patch must remove both gates — otherwise the feature gets declared but TILED
does not.

In the version that ships in the Armada image (26.2.3) the second gate does not
exist yet: only the exposure is patched there. Moving to a fresher Mesa, there
will be two changes.

## 6. Reproduction

`tests/rp6-vkd3d-sparse-test.c` — the test on which all the results above were
obtained. Built on stock Mesa 26.2.2 / A740 on 20–21.09.2026; the full results
matrix and the verdict are in the file header.

Key probes: **A** — viability of the sparse machinery; **S** — the hole (on
stock the data gets corrupted, on 0004 create is rejected, on 0005 both views
are exact); **S2** — the boundary (mixed-swap and the NULL list remain
rejected); **T** — P6.

Build and run instructions are in the file header. The device has no compiler,
it is built in a container.

## 7. Swapping the driver on the device

No edits are made to files under `/usr` on the device. A patched file in `/usr`
is a divergence from the installed package: it breaks installation
verification, and after an image update it surfaces as system corruption — not
at the moment of the edit, but later and in a different update, so the link to
the cause is lost.

Therefore the driver is swapped by **temporary mounting**: the patched file
lives outside `/usr` (e.g. in `/var/lib/rp6-mesa/`), and a bind-mount is placed
on top of the packaged one. The mount point does not survive a reboot — after
the reboot the system is package-clean again, nothing needs to be cleaned up,
and the update goes through cleanly.

```sh
sudo mount --bind /var/lib/rp6-mesa/libvulkan_freedreno.so \
  /usr/lib64/libvulkan_freedreno.so   # after reboot the mount is gone
```

If the swap is needed permanently within a session, the mounting is set up as
a unit in `/etc/systemd/system` — `/usr` is not touched in that case either.
For vkd3d-proton, if a library swap is needed, the same trick applies: the file
lives outside the prefix and is mounted in place.
