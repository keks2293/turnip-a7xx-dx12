# `fd_dev_info.props` properties by A7XX sub-generation

Sources:

- `src/freedreno/common/freedreno_devices.py` — the `a7xx_gen1`, `a7xx_gen2`, `a7xx_gen3` blocks
- `src/freedreno/common/freedreno_dev_info.h` — field types and comments
- `src/freedreno/vulkan/`, `src/freedreno/ir3/` — actual usage

Device mapping (per `freedreno_devices.py`):

| block | devices | chip |
|---|---|---|
| `a7xx_gen1` | FD730 | Adreno 730 |
| `a7xx_gen2` | X1-45/FD735, **FD740 + X1-85 (ours)**, FDA32, FD740v3/Quest 3 | Adreno 740 and relatives |
| `a7xx_gen3` | FD750, 810/829/830/840/X2-85/X2-90 | Adreno 750+ |

**How to read the "default".** The `fd_dev_info` structure in the generated
`freedreno_devices.h` is a designated initializer: a field that no `GPUProps`
sets is not printed and equals `0` in C. For a7xx the property list is
`[a7xx_base, a7xx_genX]`, i.e. `a6xx_base` is **not included**, and `a7xx_base`
sets none of the properties in the table below. Therefore the default for every
bool is `False`, while `max_draw_states` is a special case (see the note after
the table).

Rows are ordered by the group in which they appear: `123` → `12.` → `1..` → `.2.` → `.23` → `..3`.

| # | property | type | gen1 | gen2 | gen3 | default | comment from `freedreno_dev_info.h` | description (what it does) |
|---|---|---|---|---|---|---|---|---|
| 1 | `supports_uav_ubwc` | bool | True | True | True | False | Whether UBWC is supported on all UAVs. Prior to this, only readonly or writeonly UAVs could use UBWC and mixing reads and writes was not permitted. | UBWC allowed on any UAV; previously only for readonly/writeonly, mixing reads and writes was forbidden. |
| 2 | `enable_tp_ubwc_flag_hint` | bool | True | False | — | False | On a740 TPL1_DBG_ECO_CNTL1.TP_UBWC_FLAG_HINT must be the same between all drivers in the system, somehow having different values affects BLIT_OP_SCALE. We cannot automatically match blob's value, so the best thing we could do is a toggle. | Whether to program `TPL1_DBG_ECO_CNTL1.TP_UBWC_FLAG_HINT`. The value must match across all drivers in the system, otherwise `BLIT_OP_SCALE` breaks. On **our FD740 it is False**; True is set only on X1-45/FD735 and Quest 3. Forcing `True` on ours gives artifacts in RE4 (section 15.4 `analysis.md`), 0% in vkmark. |
| 3 | `fs_must_have_non_zero_constlen_quirk` | bool | True | True | — | False | Having zero consts in one FS may corrupt consts in follow up FSs, on such GPUs blob never has zero consts in FS. The mechanism of corruption is unknown. | Ban on a zero constlen for the fragment shader: zero constants in one FS corrupts the constants of the following FSs (corruption in ir3). |
| 4 | `reading_shading_rate_requires_smask_quirk` | bool | True | True | — | False | A7XX gen1 and gen2 seem to require declaring SAMPLEMASK input for fragment shading rate to be read correctly. This workaround was seen in the prop driver v512.762.12. | The `SAMPLEMASK` input must be declared for fragment shading rate to be read correctly. Found in blob v512.762.12. |
| 5 | `cs_lock_unlock_quirk` | bool | True | — | — | False | Is lock/unlock sequence needed at end of compute shader? | `OPC_LOCK` / `OPC_UNLOCK` are inserted before the end of the compute shader (`ir3_compiler_nir.c:6194`). |
| 6 | `stsc_duplication_quirk` | bool | — | True | — | False | A7XX / gen7 stsc may need to be done twice for the same range to workaround _something_, observed in blob's disassembly. | The STSC (state-table config) write for one range is done twice. Observation from the blob's disassembly. |
| 7 | `ubwc_unorm_snorm_int_compatible` | bool | — | True | True | False | Whether the UBWC fast-clear values for snorn, unorm, and int formats are the same. This is the case from a740 onwards. These formats were already otherwise UBWC-compatible, so this means that they are now fully compatible. | UBWC fast-clear yields the same value for snorm/unorm/int → these formats are fully compatible with one another (from a740 on). |
| 8 | `has_64b_image_atomics` | bool | — | True | True | False | — | 64-bit atomics in images → the `EXT_shader_image_int64_atomics` / `EXT_shader_image_atomic_int64` feature (`tu_device.cc:378,774`, `tu_formats.cc:303`). |
| 9 | `has_64b_ssbo_atomics` | bool | — | True | True | False | — | 64-bit atomics in SSBOs → the `VK_KHR_shader_atomic_int64` feature (`tu_device.cc:274,516`). |
| 10 | `has_event_write_sample_count` | bool | — | True | True | False | Whether there is CP_EVENT_WRITE7::WRITE_SAMPLE_COUNT | Presence of pkt7 `CP_EVENT_WRITE7::WRITE_SAMPLE_COUNT`. |
| 11 | `has_hw_bin_scaling` | bool | — | True | True | False | a740+ support a per-view list of bin scales in GRAS which can be used to modify the viewport, rather than manually patching it in the driver. | A hardware per-view list of bin scales in GRAS instead of manually patching the viewport in the driver. |
| 12 | `has_image_processing` | bool | — | True | True | False | Whether the device supports the image processing opcode | Presence of the image processing opcode. |
| 13 | `has_implicit_fragface_fragcoord_ij_linear` | bool | — | True | True | False | Whether GRAS_CL_INTERP_CNTL has FACENESS/CENTERRHW and thus being able to avoid setting ij_linear_sample for FragFace/FragCoord. | `GRAS_CL_INTERP_CNTL` contains FACENESS/CENTERRHW → no need to set `ij_linear_sample` for FragFace/FragCoord. |
| 14 | `has_primitive_shading_rate` | bool | — | True | True | False | — | The `primitiveFragmentShadingRate` feature (`VK_EXT_fragment_shading_rate`) — `tu_device.cc:596,1349`. |
| 15 | `has_ray_intersection` | bool | — | True | True | False | Whether the ray_intersection instruction is present. | Presence of the `ray_intersection` instruction (ray intersection) — the basis for ray tracing. |
| 16 | `has_generic_clear` | bool | — | — | True | False | Whether a single clear blit could be used for both sysmem and gmem. | One clear-blit works for both sysmem and GMEM → generic clear instead of chip-specific. Anchors: `tu_clear_blit.cc:4013` (`use_generic_clear_for_image_clear`), `:4030` (event vs cp blit), `:4051` (flush `CCU_INVALIDATE_COLOR\|WAIT_FOR_IDLE`), `:4866` (`tu_clear_attachments_generic`), `tu_cmd_buffer.cc:6690/719` (separate clears are not emitted), `tu_pass.cc:560` (conditional load/store is disabled), `tu_query_pool.cc:1574` (`CCU_INVALIDATE_DEPTH`). **Enabled by default for gen2 together with #17 — patch 0008**; measurements: synthetic −61…−65%, RE4 0%, frames without corruption (section 15 `analysis.md`). |
| 17 | `r8g8_faulty_fast_clear_quirk` | bool | — | — | True | False | Whether r8g8 UBWC fast-clear work correctly. | Inverted flag: UBWC fast-clear **does not work correctly for R8G8**, so the guard in `use_generic_clear_for_image_clear` disallows generic clear for `image_is_r8g8` (`tu_clear_blit.cc:3008,4013`). The only guard on R8G8. It only makes sense in pair with #16: without generic clear the condition (`has_generic_clear && !(quirk && image_is_r8g8)`) is false either way, i.e. the quirk alone is a no-op. **In pair, enabled by default for gen2 — patch 0008**: the pair is free (0% in vkmark and RE4), while without the quirk R8G8 clears via the generic path cause GPU faults (fast-clear + sizes like 960x540 + GMEM renderpass). |
| 18 | `load_shader_consts_via_preamble` | bool | — | — | True | — | — | Shader constants and bindless base addresses are loaded in the preamble rather than by a separate draw-state CONST (`tu_cmd_buffer.cc:1827,7530,7548`). **Corrupts on a740**: forcing gives `0x00000000` on every read in the test (section 14.3 `analysis.md`; reproduced on 30.09 — the bit, both in combination with #19 and alone). |
| 19 | `load_inline_uniforms_via_preamble_ldgk` | bool | — | — | True | — | — | The inline UBO (descriptor memory) is loaded by the `LDGK` instruction from the preamble (`tu_shader.cc:1097,1307`, `tu_cmd_buffer.cc:7674,7735`). The test matches the reference byte for byte, the in-game measurement (30.09, RE4) is **0** (section 15.5 `analysis.md`). |
| 20 | `has_gmem_vpc_attr_buf` | bool | — | — | True | False | — | Enables a separate VPC attribute buffer; emitted at the start and on CCU state change (`tu_cmd_buffer.cc:598,739,2332,2450`). |
| 21 | `sysmem_vpc_attr_buf_size` | uint32_t | — | — | 0x20000 | 0 | Size of various in-gmem caches: | Size of the VPC attr buf in sysmem mode (`fd6_gmem_cache.h:92,99`). |
| 22 | `gmem_vpc_attr_buf_size` | uint32_t | — | — | 0xc000 | 0 | — | Size of the VPC attr buf in GMEM mode (`fd6_gmem_cache.h:84,102`); programmed via `VPC_ATTR_BUF_GMEM_SIZE` (`tu_cmd_buffer.cc:607`). |
| 23 | `ubwc_all_formats_compatible` | bool | — | — | True | False | A750+ added a special flag that allows HW to correctly interpret UBWC, including UBWC fast-clear when casting image to a different format permitted by Vulkan. So it's possible to have UBWC enabled for image that has e.g. R32_UINT and R8G8B8A8_UNORM in the mutable formats list. | The a750 flag allows UBWC + fast-clear when casting the format through the mutable format list. **Must not be enabled on a740** — damage from two independent parts (sections 14.3 and 14.5 `analysis.md`): (1) `vkCreateImage` for MUTABLE+SPARSE starts returning `SUCCESS` with a linear sparse image instead of `FORMAT_NOT_SUPPORTED`; (2) UBWC reinterpretation between formats does not work in principle — `H d1'` (an R32_UINT view over RGBA8) yields garbage `0x079d685e…` **both with and without `MUTABLEEN`** (experiment 0005, `results/test-expE.log`). The corruption from the `MUTABLEEN` bit itself lies entirely in `swap` (261120/262144 → 0/262144 after hack 0004, `results/test-expD*.log`), and it is fixed only by removing the damage it itself causes: the test showed no case where the flag did better than base. |
| 24 | `ubwc_coherency_quirk` | bool | — | — | True | False | a750 has a bug where writing and then reading a UBWC-compressed UAV requires flushing UCHE. This is reproducible in many CTS tests, for example dEQP-VK.image.load_store.with_format.2d.*. | a750: after writing to a UBWC UAV, UCHE must be flushed before reading. Added to `TU_FORCE_PROPS` (patch 0007) and run in the combo — **changes nothing**: with `ubwc_all_formats_compatible` the log matches byte for byte the one with that flag alone (section 14.4 `analysis.md`). |
| 25 | `has_compliant_dp4acc` | bool | — | — | True | False | — | `DP4ACC` gives spec-compliant `sdot`/`udot` 4x8 behaviour → the NIR options `has_sdot_4x8[_sat]` get enabled (`ir3_compiler.c:406`, `ir3_compiler_nir.c:436`). |
| 26 | `has_abs_bin_mask` | bool | — | — | True | False | Whether CP_SET_BIN_DATA5::ABS_MASK exists | Presence of the `ABS_MASK` field in pkt7 `CP_SET_BIN_DATA5`. |
| 27 | `has_persistent_counter` | bool | — | — | True | False | Whether CP_ALWAYS_ON_COUNTER only resets on device loss rather than on every suspend/resume. | `CP_ALWAYS_ON_COUNTER` resets only on device loss, not on every suspend/resume → suitable for autotune counters. |
| 28 | `gs_vpc_adjacency_quirk` | bool | — | — | True | False | On a750 there is a hardware bug where certain VPC sizes in a GS with an input primitive type that is a triangle with adjacency can hang with a high enough vertex count. | a750: certain VPC sizes in a GS with triangles+adjacency hang at a high enough vertex count. |
| 29 | `has_alias_rt` | bool | — | — | True | False | Whether alias.rt is supported. | Support for the `alias.rt` instruction. |
| 30 | `has_rt_workaround` | bool | — | — | True | False | a750-specific HW bug workaround for ray tracing | Workaround for an a750-specific ray tracing HW bug. |
| 31 | `has_sw_fuse` | bool | — | — | True | False | Whether features may be fused off by the SW_FUSE. So far, this is just raytracing. | Features can be fused off via SW_FUSE; so far this is only raytracing. |
| 32 | `new_control_regs` | bool | — | — | True | False | On a750 the control register layout is rearranged. | On a750 the control register layout is rearranged. |
| 33 | `max_draw_states` | uint32_t | — | — | 64 | 0 | The amount of valid draw state IDs. | The number of valid draw state IDs. The only use is `tu_autotune.cc:339`: `max_draw_states > TU_DRAW_STATE_AT_WRITE_RP_HASH` enables the autotune flag `PREEMPT_OPTIMIZE`. |

Totals: **gen1 = 5, gen2 = 14, gen3 = 28.**

## Notes

### `max_draw_states` is 0 on a740

`max_draw_states = 32` is set only in `a6xx_base` (`freedreno_devices.py:152`).
a7xx devices use the list `[a7xx_base, a7xx_genX]` — `a6xx_base` is **not** in
it, and `a7xx_base` does not set this field. In the generated
`freedreno_devices.h` the field is absent for six a7xx entries and present
(`64`) only in `__info26` (gen3).

Consequence: `TU_DRAW_STATE_COUNT = 19 + 13 = 32`, hence
`TU_DRAW_STATE_AT_WRITE_RP_HASH = 33`, and the condition `0 > 33` is false — on
a730/a740 `PREEMPT_OPTIMIZE` **is off by default**. It gets enabled only by
`TU_MAX_DRAW_STATES=64` (patch 0007); this does not affect FPS (section 14
`analysis.md`).

The number 128 came up earlier in the discussion — it is not in the code.

### Coverage of `TU_FORCE_PROPS`

`tu_force_props()` (`tu_device.cc:1717`) can override 12 flags:

```
load_shader_consts_via_preamble, load_inline_uniforms_via_preamble_ldgk,
has_generic_clear, has_gmem_vpc_attr_buf, ubwc_all_formats_compatible,
has_compliant_dp4acc, has_persistent_counter, has_abs_bin_mask,
ubwc_coherency_quirk, cs_lock_unlock_quirk, enable_tp_ubwc_flag_hint,
r8g8_faulty_fast_clear_quirk
```

plus `TU_MAX_DRAW_STATES` (a number) separately. Every hit writes
`TU_FORCE_PROPS: <name>` to the log — from these lines you can see that the
variable reached the driver (checked on in-game runs, section 15.4
`analysis.md`).

- When forced, `has_gmem_vpc_attr_buf` fills in the sizes `0x20000`/`0xc000`
  itself if they were not set explicitly (`tu_device.cc:1753`).
- `r8g8_faulty_fast_clear_quirk` was added to the list to check whether the
  R8G8 protection negates the gain of `has_generic_clear` — it does not
  (`gc_r8g8 ≈ gc`, section 15.2 `analysis.md`).
- Forcing `enable_tp_ubwc_flag_hint` to `True` gives **artifacts in RE4**
  (section 15.4 `analysis.md`); no corruption in vkmark or the test.
- Forcing `ubwc_all_formats_compatible` and `load_shader_consts_via_preamble`
  produces corruption (verified with the `rp6-vkd3d-sparse-test` test).
- `load_inline_uniforms_via_preamble_ldgk` is the "preamble" prop that passes
  the check: the test matches the reference byte for byte, 0 in game
  (section 15.5 `analysis.md`).
- `ubwc_coherency_quirk` was added to the list to check whether it would remove
  the corruption caused by `ubwc_all_formats_compatible` — it will not: the
  combo gives byte for byte the same log as `ubwc_all_formats_compatible` alone
  (section 14.4 `analysis.md`).
  There are no other props in this path.

### `enable_tp_ubwc_flag_hint`: why True shows up elsewhere

The raw `a740_raw_magic_regs` contains `TPL1_DBG_ECO_CNTL1 = 0x00040724`
(bit 18 `TP_UBWC_FLAG_HINT` is set), while the gen2 prop clears it → what ends
up in use is `0x00000724`. Git history:

- `d853443a2ea3` (17.06.2024): *«Most devices with a740 have blob v6xx which
  doesn't have TP_UBWC_FLAG_HINT set. Match them for better compatibility»* —
  the prop was set to `False` to match blob v6xx.
- `7968b356f8c` (19.09.2024, closes `#10316`): fixes A740v3/Quest 3 — the
  chip_id didn't match, Quest 3 ran like a plain a740 and corrupted the image;
  the fix given is `RB_DBG_ECO_CNTL = 1`, and the hint was enabled because
  *«Quest 3 ships with blob version 7xx»*.

So **one and the same a740 silicon is split across products by blob**: phones
(v6xx) → `False`, Quest 3 (7xx) → `True`. The value `0x00040724` in the raw
list is the state of an old/someone else's dump, not the behavior of our blob,
so "matching the blob" is not verifiable here and the prop was made a toggle.
On a730 (`gen1`) the raw value is the same, and `True` is the same state as on
Quest 3.

For gen3 the prop is dead: in the raw list for FD750 (`# Values from blob v676.0`)
`TPL1_DBG_ECO_CNTL1` is absent entirely, i.e. the register is never programmed
and the bit value does not change in any prop state.

Practice (section 15 `analysis.md`): on our a740 forcing `True` gives 0% in
vkmark and **artifacts in RE4** — it must not be enabled, `False` remains
correct.

### What "—" does and does not say

`—` = the property is not set in the block → equals `0`/`False` in C. This is
**an observation from the Qualcomm blob for a particular chip**, not a split by
generation: formally the 830 does not take part in the table, and
`enable_tp_ubwc_flag_hint` also differs within gen2 (True on X1-45 and Quest 3,
False on FD740).
