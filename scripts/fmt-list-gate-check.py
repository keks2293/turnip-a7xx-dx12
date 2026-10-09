#!/usr/bin/env python3
"""Who actually ends up in linear with a mutable format list.

Transfers to Python exactly those predicates that decide the fate of an image in
`tu_image_init()` (src/freedreno/vulkan/tu_image.cc) and
`tu6_mutable_format_list_forces_linear()` (tu_formats.cc):

  tu6_mutable_format_list_ubwc_compatible()  -> early exit, the whole block is skipped
  fd6_ubwc_compat_mode()                     -> src/freedreno/common/freedreno_ubwc.h
  tu6_format_list_has_swaps()                -> swap from fd6_format_table.c, TILE6_LINEAR
  tu6_format_list_reinterprets_r8g8_r16()    -> blocksize / nr_components
  tu6_format_list_swaps_are_uniform()        -> patch 0005
  NV12 branch                                -> patch 0009 removes force_linear_tile
  FD6_UBWC_B8G8R8A8_INT                      -> patch 0006

Experiments (files exp-N in experiments/, flags --exp-N):
  --exp-9  a single-channel format (R32_UINT and the like) does not push the
             list into linear: it has no channel order to argue about.
             Changes tu6_format_list_swaps_are_uniform().
  --exp-10  in the !mutable_ubwc_fc branch is_mutable = true is set
             (MUTABLEEN without UBWC). Changes only tu_image_init().

The early exit is the main thing: if the list is "UBWC-compatible", `has_swaps`
is never reached, and neither the stock nor the patches change anything. The
enumeration that ignores this early exit gives wrong numbers (that is what
happened in the first analysis of §16.2).

GPU properties come from freedreno_devices.py: FD740 = [a7xx_base, a7xx_gen2],
so ubwc_unorm_snorm_int_compatible = True, ubwc_all_formats_compatible = 0.

Run:
  scripts/fmt-list-gate-check.py [--table PATH/fd6_format_table.c]
                                 [--classes tests/vkd3d-fmt-classes.h]
"""

import argparse
import itertools
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TABLE = "/var/home/armada/opencode/build/mesa-rp6/src/freedreno/fdl/fd6_format_table.c"

UNKNOWN = "UNKNOWN_COMPAT"
NV12_VK = "G8_B8R8_2PLANE_420_UNORM"

# a7xx_gen2 (FD740) from src/freedreno/common/freedreno_devices.py.
# has_8bpp_ubwc in the a7xx chain ([a7xx_base, a7xx_gen2]) is nowhere set —
# the C struct is zero-filled, i.e. False by default (NOT inherited from a6xx_gen2).
# supports_uav_ubwc = True sits right in a7xx_gen2, so for UAV images the list
# gate is indeed the limiting factor, while for the 8-bits-per-block ones it is not.
# Measurement (2026-10-02, FD_DEV_FEATURES=has_8bpp_ubwc=1): the memreq ceiling for R8 exists
# (0x40000 -> 0x42000), but the content under UBWC is corrupted (roundtrip — all zeros),
# i.e. the flag is protection against corruption, keep False. results/test-r8-has8bpp-*.log.
GEN2_PROPS = {"ubwc_unorm_snorm_int_compatible": True, "ubwc_all_formats_compatible": False,
              "has_8bpp_ubwc": False, "supports_uav_ubwc": True}

# Block shapes for which the stock fd6_ubwc_compat_mode() has rows:
# (number of channels, bits per channel). Taken from the case list of the header.
SHAPES_IN_BLOB = {(2, 8), (4, 8), (2, 16), (4, 16), (1, 32), (2, 32), (4, 32)}

# Formats for which UBWC is impossible regardless of the gate (tu6_ubwc_possible).
NO_UBWC_ALWAYS = ("BC", "ASTC", "ETC2", "EAC", "PVRTC", "G8_B8R8_2PLANE",
                  "G8_B8R8A8_2PLANE", "G16_B16R16_2PLANE_420", "G12B12G12B12_2PLANE_420",
                  "G10X6", "G16_B16R16_2PLANE_422", "G12B12G12B12_2PLANE_422",
                  "G24X8", "G24Y8", "R64_UINT", "R64_SINT", "R64G64", "E5B9G9R9_UFLOAT_PACK32",
                  "S8_UINT")


def is_compressed(f):
    """Block compression or planar video: UBWC is impossible for them per
    tu6_ubwc_possible() regardless of what the gate decided."""
    return norm(f).startswith(NO_UBWC_ALWAYS)


def why_unknown(f, props):
    """Why fd6_ubwc_compat_mode() returns UNKNOWN for this format.

    This is the key to the next lever: UNKNOWN means "the blob table has no row",
    and therefore NO rule in the driver fixes it — you either have to find an
    equivalent class that already exists, or admit that the question goes to the hardware.
    """
    f, g = norm(f), stem(f)
    if is_compressed(f):
        return "compressed/planar: UBWC impossible"
    if re.fullmatch(r"R8(?:_[A-Z]+)?", g):
        return "8 bits per block: has_8bpp_ubwc=False (protection against corruption; the memreq ceiling exists, content under UBWC — all zeros, test-r8-has8bpp-on.log)"
    if g in ("Z32_FLOAT", "Z24_UNORM_S8_UINT", "D24_UNORM_S8_UINT", "D32_SFLOAT",
             "X8_D24_UNORM_PACK32", "D16_UNORM"):
        return "depth: separate clear semantics"
    if f in ("R64_UINT", "R64_SINT") or g.startswith("R64G64"):
        return "R64: UBWC forbidden"
    if g.startswith("R16_UINT") or g.startswith("R16_SINT") or g.startswith("R16_SNORM") \
            or g == "R16_UNORM":
        return "single-channel 16 bit: no row in blob"
    if g.startswith("R10G10B10A2") or g.startswith("A2B10G10R10") \
            or g.startswith("A2R10G10B10") or g.startswith("R11G11B10") \
            or g.startswith("R5G6B5") or g.startswith("B5G6R5") or g.startswith("B5G5R5A1") \
            or g.startswith("B4G4R4A4") or g.startswith("R4G4B4A4"):
        return "packed-10/5/4 bit: no row in blob"
    if g.startswith("R32G32B32") and "A32" not in g:
        return "3-component 32 bit: no row in blob"
    if re.fullmatch(r"R8G8B8_[A-Z]+", g) or re.fullmatch(r"B8G8R8_[A-Z]+", g):
        return "3-component 8 bit: no row in blob"
    if re.fullmatch(r"R16G16B16_[A-Z]+", g) or re.fullmatch(r"B16G16R16_[A-Z]+", g):
        return "3-component 16 bit: no row in blob"
    if g.startswith("R32_SFLOAT") or g.startswith("R32G32_FLOAT") \
            or g.startswith("R32G32B32A32_FLOAT") or g.startswith("R16G16_FLOAT") \
            or g.startswith("R16G16B16A16_FLOAT"):
        return "_SFLOAT outside the integer classes (patch 0012)"
    if g.startswith("B8G8R8A8") or g.startswith("B8G8R8X8"):
        return "B8G8R8A8 family outside the UNORM class (patch 0006)"
    if re.fullmatch(r"[RGBA]\d+[RGBA]\d+[RGBA]\d+[RGBA]?\d*_[A-Z0-9_]+", g):
        return "no row in blob"
    return "no row in blob"


def load_format_table(path):
    """(pipe_format -> swap) for TILE6_LINEAR: the third argument _T_/VTC/TC."""
    src = open(path).read()
    swap = {}
    srgb = {}
    for m in re.finditer(r"(TC|_TC)\(\s*(\w+),\s*(\w+),\s*(\w+)\)", src):
        kind, fmt, texfmt, sw = m.groups()
        if fmt in swap:
            continue
        swap[fmt] = sw
        srgb[fmt] = kind == "_TC"
    return swap, srgb


def fd6_ubwc_compat_mode(fmt, props, patch_0006=False, patch_0010=False, patch_0011=False):
    """Port of fd6_ubwc_compat_mode() from freedreno_ubwc.h (stock).

    patch_0010 — patch 0012 (used to be exp-8): include the _SFLOAT
    members in the integer compat classes.  Stock does not include them, and for R32_FLOAT it says directly
    «a630 blob allows these, but not a660» (a660 == our gen2 family),
    so this is a hypothesis test, not a port of code.
    """
    fmt = norm(fmt)
    ok = props["ubwc_unorm_snorm_int_compatible"]

    def pair(plain, inttype):
        return inttype if ok else plain

    if fmt in ("R8G8_UNORM", "R8G8_SRGB"):
        return pair("R8G8_UNORM", "R8G8_INT")
    if fmt == "R8G8_SNORM":
        return pair(UNKNOWN, "R8G8_INT")
    if fmt in ("R8G8_UINT", "R8G8_SINT"):
        return "R8G8_INT"

    if fmt in ("R8G8B8A8_UNORM", "R8G8B8A8_SRGB"):
        return pair("R8G8B8A8_UNORM", "R8G8B8A8_INT")
    if fmt == "R8G8B8A8_SNORM":
        return pair(UNKNOWN, "R8G8B8A8_INT")
    if fmt in ("R8G8B8A8_UINT", "R8G8B8A8_SINT"):
        return "R8G8B8A8_INT"

    if fmt == "R16G16_UNORM":
        return pair("R16G16_UNORM", "R16G16_INT")
    if fmt == "R16G16_SNORM":
        return pair(UNKNOWN, "R16G16_INT")
    if fmt in ("R16G16_UINT", "R16G16_SINT"):
        return "R16G16_INT"

    if fmt in ("R16G16B16A16_UNORM", "R16G16B16A16_SRGB"):
        return pair("R16G16B16A16_UNORM", "R16G16B16A16_INT")
    if fmt == "R16G16B16A16_SNORM":
        return pair(UNKNOWN, "R16G16B16A16_INT")
    if fmt in ("R16G16B16A16_UINT", "R16G16B16A16_SINT"):
        return "R16G16B16A16_INT"

    if fmt in ("R32_UINT", "R32_SINT"):
        return "R32_INT"
    if fmt in ("R32G32_UINT", "R32G32_SINT"):
        return "R32G32_INT"
    if fmt in ("R32G32B32A32_UINT", "R32G32B32A32_SINT"):
        return "R32G32B32A32_INT"

    if patch_0011:
        # Single-channel 16-bit and packed-10-bit.  No row, not even a
        # mention in fd6_format_table.c, but a measurement on 740 (results/test-CEIL-
        # stock.log, CEIL) shows: a lone R16_UNORM and
        # A2B10G10R10_UNORM_PACK32 WITHOUT the list do get UBWC metadata, that is
        # the ceiling exists, and it is exactly the gate that lifts it.  All four variants of
        # the same block size (16 bits) differ only in interpretation.
        if fmt in ("R16_UNORM", "R16_SNORM", "R16_UINT", "R16_SINT",
                   "R16_FLOAT", "R16_SFLOAT"):
            return "R16_INT"
        # norm() strips _PACK32, so the names are already without the suffix
        if fmt in ("A2B10G10R10_UNORM", "A2B10G10R10_UINT",
                   "A2B10G10R10_SNORM", "R11G11B10_FLOAT"):
            return "A2B10G10R10_INT"

    if patch_0010:
        # a cast within the same layout — the same bits, another reading.
        # Z32_FLOAT is NOT touched: it has another role (depth), and stock
        # merges it with R32_FLOAT only to mark it as UNKNOWN.
        # names arrive both in pipe form (_FLOAT) and in Vulkan form (_SFLOAT)
        if fmt in ("R32_FLOAT", "R32_SFLOAT"):
            return "R32_INT"
        if fmt in ("R16G16_FLOAT", "R16G16_SFLOAT"):
            return "R16G16_INT"
        if fmt in ("R16G16B16A16_FLOAT", "R16G16B16A16_SFLOAT"):
            return "R16G16B16A16_INT"
        if fmt in ("R32G32_FLOAT", "R32G32_SFLOAT"):
            return "R32G32_INT"
        if fmt in ("R32G32B32A32_FLOAT", "R32G32B32A32_SFLOAT"):
            return "R32G32B32A32_INT"

    if fmt in ("Z32_FLOAT", "R32_FLOAT"):
        return UNKNOWN

    # B8G8R8A8 family: stock — only UNORM/SRGB, stock B8G8R8A8_UNORM
    if fmt in ("B8G8R8A8_UNORM", "B8G8R8A8_SRGB"):
        return "B8G8R8A8_INT" if (patch_0006 and ok) else "B8G8R8A8_UNORM"
    if patch_0006:
        if fmt == "B8G8R8A8_SNORM":
            return "B8G8R8A8_INT" if ok else UNKNOWN
        if fmt in ("B8G8R8A8_UINT", "B8G8R8A8_SINT"):
            return "B8G8R8A8_INT"

    return UNKNOWN


def ubwc_compatible(formats, props, patch_0006, patch_0010=False, patch_0011=False):
    """tu6_mutable_format_list_ubwc_compatible(): True => the whole block is skipped."""
    if not formats:
        return False
    if len(formats) == 1:
        return True
    first = fd6_ubwc_compat_mode(formats[0], props, patch_0006, patch_0010, patch_0011)
    if first == UNKNOWN:
        return False
    return all(fd6_ubwc_compat_mode(f, props, patch_0006, patch_0010, patch_0011) == first
               for f in formats[1:])


def ubwc_groups(formats, props, patch_0006=False, patch_0010=False, patch_0011=False):
    """Split of the list by compat modes: [(mode, [formats]), ...]."""
    groups = {}
    for f in formats:
        groups.setdefault(fd6_ubwc_compat_mode(f, props, patch_0006, patch_0010, patch_0011), []).append(f)
    return sorted(groups.items(), key=lambda kv: (kv[0] == UNKNOWN, kv[0]))


def blockers(formats, props, patch_0006=False, patch_0010=False, patch_0011=False):
    """Signature of what breaks the gate, plus the blocking pairs themselves.

    The signature — a sorted list of (mode, first format of that mode).
    Formats with UNKNOWN are moved to the end: for them the a630/a660 blob
    table has no row at all, so no rule extension will close them.
    """
    groups = ubwc_groups(formats, props, patch_0006, patch_0010, patch_0011)
    sig = tuple((mode, fmts[0]) for mode, fmts in groups)
    pairs = []
    for i, (m1, f1s) in enumerate(groups):
        for m2, f2s in groups[i + 1:]:
            pairs.append((f1s[0], m1, f2s[0], m2))
    return sig, pairs, groups


def report_blockers(swap, classes, props, patch_0006=False, patch_0010=False, patch_0011=False):
    """Analysis of classes where the gate removes UBWC but tiling stays."""
    rows = []
    for name, fmts in classes:
        if ubwc_compatible(fmts, props, patch_0006, patch_0010, patch_0011):
            continue
        sig, pairs, groups = blockers(fmts, props, patch_0006, patch_0010, patch_0011)
        rows.append((name, fmts, sig, pairs, groups))
    if not rows:
        print("\n### UBWC blockers: there are no such classes\n")
        return
    print(f"\n### What blocks UBWC removal: {len(rows)} classes\n")
    print("A. Not a gate blocker: UBWC is impossible for the format in principle "
          "(tu6_ubwc_possible()).\n")
    hard = [r for r in rows if any(is_compressed(f) for f in r[1])]
    for name, fmts, sig, pairs, groups in hard:
        base = fmts[0]
        print(f"  [{len(fmts)}] {name:26} base={base:16} {why_unknown(base, props)}")
    print(f"  total unreachable: {len(hard)} classes out of {len(rows)}\n")

    print("B. The blocker is the gate itself: the list goes into a mode without UBWC, "
          "although each format on its own\n   does hold UBWC. This is the only group "
          "where editing the rule makes sense.\n")
    soft = [r for r in rows if r not in hard]
    buckets = {}
    for name, fmts, sig, pairs, groups in soft:
        buckets.setdefault(sig, []).append((name, fmts, pairs))
    for sig, lst in sorted(buckets.items(), key=lambda x: -len(x[1])):
        head = " + ".join(f"{m}({f})" for m, f in sig)
        print(f"  [{len(lst):3}] {head}")
        for name, fmts, pairs in lst:
            why = why_unknown([f for m, f in sig if m == UNKNOWN][0], props) \
                if any(m == UNKNOWN for m, _ in sig) else "?"
            print(f"          {name:26} n={len(fmts):2}  UNKNOWN reason: {why}")
    print(f"\n  total blocked by the gate: {len(soft)} of {len(rows)} classes")
    return rows


def report_0011(swap, classes, props):
    """What exp-11 changes: rows for R16_* and A2B10G10R10_*.

    The basis is a measurement, not reasoning.  The CEIL probe (results/test-CEIL-
    stock.log) showed that a lone R16_UNORM and A2B10G10R10_UNORM_PACK32 without
    a list get UBWC metadata: the ceiling exists.  The list lifts it, because
    fd6_ubwc_compat_mode() keeps silence for them.  Unlike 0012, here you do not
    need to claim that the blob «allows but not a660»: the formats are not
    mentioned in the blob table at all, and A2B10G10R10 is not even in fd6_format_table.c.
    """
    closed = []
    for name, fmts in classes:
        if ubwc_compatible(fmts, props, False, patch_0010=True):
            continue
        if not ubwc_compatible(fmts, props, False, patch_0010=True, patch_0011=True):
            continue
        # tu_image_init() returns (Layout, whether the early exit fired), and the
        # verdict here is about UBWC, i.e. about the early exit.
        _, early_before = tu_image_init(fmts[0], fmts, swap, props, patch_0010=True)
        _, early_after = tu_image_init(fmts[0], fmts, swap, props, patch_0010=True,
                                       patch_0011=True)
        closed.append((name, fmts, "UBWC removed by the gate" if not early_before else "UBWC",
                       "tiled+UBWC" if early_after else "??"))
    print(f"\n### Experiment 11: closes {len(closed)} classes\n")
    for name, fmts, before, after in closed:
        print(f"  {name:24} n={len(fmts)}  {before}  ->  {after}")
    return closed


def report_stack(swap, classes, props, patch_0011=False):
    """Layout of the ArmadOS working stack and analysis of the remaining tiled, no-UBWC.

    The working stack — 0004, 0005, 0006, 0009, 0010, 0012 (_SFLOAT).  0004 and 0007 do
    not affect the layout: 0004 forbids sparse, 0007 is diagnostics only.

    The groups are ordered NOT by name prefix.  The first version of this analysis
    did exactly that, and `G16_B16R16_2PLANE_420_UNORM` moved into the `R16_*`
    group, and `G8_B8R8_2PLANE_420_UNORM` — into `R8_*`, because `R16_` and `R8_`
    occur in them as substrings.  The compression check runs first, and planar
    video formats are filtered out by it.
    """
    kw = dict(patch_0005=True, patch_0006=True, patch_0010=True, exp_9=True)
    n_ubwc = n_lin = 0
    groups = {}
    for name, fmts in classes:
        L, _ = tu_image_init(fmts[0], fmts, swap, props, patch_0011=patch_0011, **kw)
        if L.ubwc:
            n_ubwc += 1
            continue
        if not L.tiled:
            n_lin += 1
            continue
        if any(is_compressed(f) for f in fmts):
            fam = "compressed and planar: ubwc_possible() = false"
        elif any(norm(f).startswith("R8_") for f in fmts):
            fam = "R8_*: has_8bpp_ubwc = false, no ceiling"
        elif any("R32G32B32" in norm(f) for f in fmts):
            fam = "R32G32B32_*: no ceiling"
        elif any(norm(f).startswith(("R16_", "A2B10G10R10", "R10G10B10A2"))
                 for f in fmts):
            fam = "R16_* / R10G10B10A2_*: the ceiling exists, the gate lifts it"
        else:
            # the case of two compat classes in one list remains: vkd3d appends
            # R32{U,I,F} itself for the typed UAV load
            fam = "two compat classes in one list (B8G8R8A8_UNORM + R32_*)"
        groups.setdefault(fam, []).append(name)

    rest = sum(len(v) for v in groups.values())
    print(f"\n### Working stack (0004, 0005, 0006, 0009, 0010"
          f"{', 0011' if patch_0011 else ''}, 0012): of {len(classes)} classes")
    print(f"  UBWC: {n_ubwc}   linear: {n_lin}   tiled, no-UBWC: {rest}\n")
    for tag, lst in sorted(groups.items(), key=lambda x: -len(x[1])):
        print(f"  [{len(lst):2}] {tag}")
        print(f"        {', '.join(lst)}")
    return groups


def geom(fmt):
    """(bytes per texel, number of channels) — for tu_is_r8g8*."""
    g = stem(fmt)
    m = re.fullmatch(r"[RAB](\d+)[RGB](\d+)[RGB](\d+)[AB](\d+)", g)
    if m:
        return sum(int(x) for x in m.groups()) // 8, 4
    m = re.fullmatch(r"[RAB](\d+)[RGB](\d+)", g)
    if m:
        return sum(int(x) for x in m.groups()) // 8, 2
    m = re.fullmatch(r"[RAB](\d+)", g)
    if m:
        return int(m.group(1)) // 8, 1
    return None, None


def channels(fmt):
    """Channels in memory order: R8G8B8A8 and B8G8R8A8 differ here."""
    g = stem(fmt)
    m = re.fullmatch(r"([RAB])(\d+)([RGB])(\d+)([RGB])(\d+)([AB])(\d+)", g)
    if m:
        return m.group(1, 3, 5, 7)
    m = re.fullmatch(r"([RAB])(\d+)([RGB])(\d+)", g)
    if m:
        return m.group(1, 3)
    m = re.fullmatch(r"([RAB])(\d+)", g)
    if m:
        return (m.group(1),)
    return ()


def is_r8g8(fmt):
    b, c = geom(fmt)
    return b == 2 and c == 2


def is_r8g8_compatible(fmt):
    b, _ = geom(fmt)
    return b == 2


def reinterprets_r8g8_r16(base, formats):
    if not formats:
        return is_r8g8_compatible(base)
    if not is_r8g8_compatible(base):
        return False
    has_r8g8 = any(is_r8g8(f) for f in formats)
    has_other = any(not is_r8g8(f) and is_r8g8_compatible(f) for f in formats)
    return has_r8g8 and has_other


def has_swaps(formats, swap):
    if not formats:
        return True
    return any(table_lookup(swap, f) not in (None, "WZYX") for f in formats)


def swaps_are_uniform(formats, swap, exp_9=False):
    """tu6_format_list_swaps_are_uniform() from patch 0005.

    exp_9 — exp-9: a single-channel format has no channel order,
    so its own swap cannot argue with a foreign one; only a match of the block
    shape (bits/width/height) is required of it, because the tile layout is a
    function of the block size (fd6_layout.c: layout->cpp).
    """
    if not formats or len(formats) < 2:
        return False
    if exp_9:
        # block shape is a block by size, not together with the channel count:
        # the tile layout depends only on util_format_get_blocksize()
        block = lambda f: geom(f)[0]
        multi = [f for f in formats if len(channels(f)) > 1]
        if not multi:
            # the whole list is single-channel: nothing to argue about
            return len({block(f) for f in formats}) == 1
        for f in formats:
            if block(f) != block(multi[0]):
                return False
            if len(channels(f)) < 2:
                continue   # no channel order — nothing to argue about
            if len(channels(f)) != len(channels(multi[0])):
                return False
            if table_lookup(swap, f) != table_lookup(swap, multi[0]):
                return False
        return True
    swaps = {table_lookup(swap, f) or "WZYX" for f in formats}
    if len(swaps) != 1:
        return False
    if len({geom(f) for f in formats}) != 1:
        return False
    if len({channels(f) for f in formats}) != 1:
        return False
    return True


class Layout:
    """What the driver decided for the image: tiling, UBWC and the MUTABLEEN bit."""

    __slots__ = ("ubwc", "tiled", "branch", "mutable")

    def __init__(self, ubwc, tiled, branch, mutable=False):
        self.ubwc, self.tiled, self.branch, self.mutable = ubwc, tiled, branch, mutable

    def __str__(self):
        s = "tiled+UBWC" if self.ubwc and self.tiled else \
            "linear, no-UBWC" if not self.tiled else "tiled, no-UBWC"
        return s + ", MUTABLEEN" if self.mutable else s

    def __format__(self, spec):
        return format(str(self), spec)


def tu_image_init(base, formats, swap, props, patch_0005=False, patch_0006=False,
                  patch_0009=True, patch_0010=False, patch_0011=False,
                  exp_9=False, exp_10=False):
    """Returns (Layout, whether the early exit fired)."""
    if ubwc_compatible(formats, props, patch_0006, patch_0010, patch_0011):
        return Layout(True, True, "early exit: list is UBWC-compatible"), True

    if base == NV12_VK:
        # stock: force_linear_tile = true; patch 0009 keeps the tiling
        return Layout(False, patch_0009, "NV12"), False

    if props["ubwc_all_formats_compatible"]:
        return Layout(True, True, "ubwc_all_formats_compatible"), False

    tiled = True
    branch = "UBWC removal only"
    if reinterprets_r8g8_r16(base, formats):
        tiled, branch = False, "r8g8↔r16"
    elif has_swaps(formats, swap) and (not patch_0005 or
                                       not swaps_are_uniform(formats, swap, exp_9)):
        tiled, branch = False, "has_swaps" + ("" if patch_0005 else " (stock)")
    # exp. 0010 sets is_mutable = true in this very branch, where UBWC is already
    # removed. It does not touch tiling, and on gen2 with diverging swaps it only
    # breaks the content (docs/analysis.md §21.4), so in the model it is marked
    # with a flag, not by substituting the layout.
    return Layout(False, tiled, branch, mutable=exp_10), False


def forces_linear(base, formats, swap, props, patch_0005=False, patch_0006=False, exp_9=False):
    """tu6_mutable_format_list_forces_linear(): sparse request from patch 0004."""
    if ubwc_compatible(formats, props, patch_0006):
        return False, "early exit"
    if re.fullmatch(r"D16|D24|D32|S8|X8_D24", base):
        return False, "depth/stencil"
    if base == NV12_VK:
        return True, "NV12"
    if props["ubwc_all_formats_compatible"]:
        return False, "ubwc_all_formats_compatible"
    if reinterprets_r8g8_r16(base, formats):
        return True, "r8g8↔r16"
    if has_swaps(formats, swap) and (not patch_0005 or
                                    not swaps_are_uniform(formats, swap, exp_9)):
        return True, "has_swaps" + ("" if patch_0005 else " (stock)")
    return False, "no trigger"


def load_vkd3d_classes(path):
    src = open(path).read()
    out = []
    for name, body in re.findall(r'\{\s*"([^"]+)"\s*,\s*\{([^}]*)\}', src):
        fmts = [x.strip().replace("VK_FORMAT_", "") for x in body.split(",") if x.strip()]
        out.append((name, fmts))
    return out


def norm(fmt):
    """Name from the pipe format table: strip the Vulkan _PACK16/_PACK32."""
    return re.sub(r"_PACK(16|32)$", "", fmt)


INTYPES = ("_UNORM", "_SNORM", "_UINT", "_SINT", "_SFLOAT", "_SRGB")


def stem(fmt):
    """Channel name without the interpretation tail: A8R8G8B8_UNORM_SRGB -> A8R8G8B8."""
    g = norm(fmt)
    while True:
        for t in INTYPES:
            if g.endswith(t):
                g = g[: -len(t)]
                break
        else:
            return g


def table_lookup(table, fmt):
    return table.get(fmt, table.get(norm(fmt)))


def zink_srgb_pairs(swap):
    """Pairs that setup_format_list() builds: format + its sRGB variant.

    Zink: srgb = util_format_srgb(templ->format), and exactly these
    two formats go into the list. util_format_linear() strips the "_SRGB" tail (or
    adds "_UNORM" if there was no tail), so the pair is always (F, F_SRGB) with
    one and the same channel order.
    """
    pairs = []
    for fmt in sorted(swap):
        if not fmt.endswith("_SRGB"):
            continue
        linear = stem(fmt) + "_UNORM"
        if table_lookup(swap, linear):
            pairs.append((linear, fmt))
    return pairs


def report_vkd3d(swap, classes, props, exp_9=False, exp_10=False):
    print(f"### vkd3d: {len(classes)} classes from tests/vkd3d-fmt-classes.h\n")
    groups = {}
    for name, fmts in classes:
        base = fmts[0]
        stock, early = tu_image_init(base, fmts, swap, props)
        p5, _ = tu_image_init(base, fmts, swap, props, patch_0005=True)
        p56, _ = tu_image_init(base, fmts, swap, props, patch_0005=True, patch_0006=True)
        p9 = tu_image_init(base, fmts, swap, props, patch_0005=True, exp_9=exp_9)[0] \
            if exp_9 else None
        p10 = tu_image_init(base, fmts, swap, props, patch_0005=True,
                            exp_9=exp_9, exp_10=exp_10)[0] if exp_10 else None
        key = (early, str(stock), str(p5), str(p56), str(p9) if p9 else "",
               str(p10) if p10 else "", stock.branch)
        groups.setdefault(key, []).append(name)
    for (early, a, b, c, d, e, branch), names in sorted(groups.items(), key=lambda x: -len(x[1])):
        head = "early exit, " if early else ""
        tail = f" -> +exp.0009 {d}" if d else ""
        tail += f" -> +exp.0010 {e}" if e else ""
        print(f"[{len(names):3}] {head}{a} -> +0005 {b} -> +0005+0006 {c}{tail}   ({branch})")
        if len(names) <= 6:
            print(f"        {', '.join(names)}")
    linear = [n for n, f in classes if not tu_image_init(f[0], f, swap, props)[0].tiled]
    print(f"\nin linear on stock: {len(linear)} of {len(classes)}")
    if exp_9:
        # what exactly exp-9 flips
        flipped = []
        for name, fmts in classes:
            base = fmts[0]
            a = tu_image_init(base, fmts, swap, props, patch_0005=True)[0]
            b = tu_image_init(base, fmts, swap, props, patch_0005=True, exp_9=True)[0]
            if a.tiled != b.tiled:
                flipped.append((name, str(a), str(b)))
        print(f"exp.0009 flips {len(flipped)} classes:")
        for name, a, b in flipped:
            print(f"        {name}: {a} -> {b}")
        left = [n for n, f in classes
                if not tu_image_init(f[0], f, swap, props, patch_0005=True, exp_9=True)[0].tiled]
        print(f"in linear with exp.0009: {len(left)} of {len(classes)} -> {', '.join(left)}")
    return linear


def report_zink(swap, props):
    pairs = zink_srgb_pairs(swap)
    print(f"\n### Zink: setup_format_list() builds {len(pairs)} pairs «format + sRGB variant»\n")
    buckets = {}
    for a, b in pairs:
        base = a
        stock, early = tu_image_init(base, [a, b], swap, props)
        p5, _ = tu_image_init(base, [a, b], swap, props, patch_0005=True)
        p56, _ = tu_image_init(base, [a, b], swap, props, patch_0005=True, patch_0006=True)
        key = (early, str(stock), str(p5), str(p56))
        buckets.setdefault(key, []).append(f"{a} + {b}")
    for key, lst in sorted(buckets.items(), key=lambda x: -len(x[1])):
        early = "early exit, " if key[0] else ""
        print(f"[{len(lst):3}] {early}{key[1]} -> +0005 {key[2]} -> +0005+0006 {key[3]}")
        for e in lst[:5]:
            print(f"        {e}")
        if len(lst) > 5:
            print(f"        … {len(lst) - 5} more")

    print("\n### Zink: other cases\n")
    cases = [
        ("ZINK_BIND_MUTABLE: MUTABLE without a list (init_ici zeroes pNext)", "R8G8B8A8_UNORM", None),
        ("video NV12: planes of the same format", NV12_VK, [NV12_VK, "R8_UNORM", "R8G8_UNORM"]),
        ("cross-order {BGRA8, RGBA8}", "R8G8B8A8_UNORM", ["B8G8R8A8_UNORM", "R8G8B8A8_UNORM"]),
        ("uniform-swap {BGRA8_UNORM, BGRA8_UINT}", "B8G8R8A8_UNORM", ["B8G8R8A8_UNORM", "B8G8R8A8_UINT"]),
        ("BGR family {B5G6R5, B5G5R5A1}", "B5G6R5_UNORM", ["B5G6R5_UNORM", "B5G5R5A1_UNORM"]),
    ]
    for name, base, fmts in cases:
        stock, _ = tu_image_init(base, fmts, swap, props)
        p5, _ = tu_image_init(base, fmts, swap, props, patch_0005=True)
        p6, _ = tu_image_init(base, fmts, swap, props, patch_0006=True)
        p56, _ = tu_image_init(base, fmts, swap, props, patch_0005=True, patch_0006=True)
        p59, _ = tu_image_init(base, fmts, swap, props, patch_0005=True, exp_9=True)
        fl_stock, _ = forces_linear(base, fmts, swap, props)
        fl_p5, _ = forces_linear(base, fmts, swap, props, patch_0005=True)
        fl_p59, _ = forces_linear(base, fmts, swap, props, patch_0005=True, exp_9=True)
        print(f"{name}\n    create:   stock {stock:16} +0005 {p5:16} +0006 {p6:16} +0005+0006 {p56:16}"
              f"\n    exp.0009: {p59:16}"
              f"\n    sparse:   stock forces_linear={fl_stock!s:5} +0005 {fl_p5!s:5} +exp.0009 {fl_p59!s:5}")
    print("\n    ZINK_BIND_MUTABLE is created when zink_format_needs_mutable() is true:")
    print("    the x8/alpha-emulation pairs give false, i.e. mutable is not needed; so the list")
    print("    is either compatible (sRGB), or absent altogether.")


def report_all_pairs(swap, props):
    fmts = sorted(swap)
    print(f"\n### All {len(fmts)}x{len(fmts) - 1} ordered format pairs from fd6_format_table.c\n")
    buckets = {}
    for a, b in itertools.permutations(fmts, 2):
        pair = [a, b]
        stock, early = tu_image_init(a, pair, swap, props)
        p5, _ = tu_image_init(a, pair, swap, props, patch_0005=True)
        p6, _ = tu_image_init(a, pair, swap, props, patch_0006=True)
        if early:
            kind = "early exit (tiled+UBWC)"
        elif not has_swaps(pair, swap):
            kind = "no swaps: UBWC is removed, tiling stays — the patches have nothing to do with it"
        elif swaps_are_uniform(pair, swap):
            kind = "uniform-swap: stock linear -> +0005 tiled -> +0006 %s" % ("linear" if not p6.tiled else "tiled+UBWC")
        else:
            kind = "cross-order: linear for everyone (cross-order experiment)"
        buckets.setdefault(kind, []).append((a, b, stock.tiled, p5.tiled, p6.tiled))
    for kind, lst in sorted(buckets.items(), key=lambda x: -len(x[1])):
        print(f"[{len(lst):6}] {kind}")
        for e in lst[:6]:
            print(f"          {e[0]} + {e[1]}  (tiled: stock={e[2]} 0005={e[3]} 0006={e[4]})")
        if len(lst) > 6:
            print(f"          … {len(lst) - 6} more")
    fixed5 = [(a, b) for lst in buckets.values() for a, b, s, p5, _ in lst if s == False and p5]
    fixed6 = [(a, b) for lst in buckets.values() for a, b, s, _, p6 in lst if s == False and p6]
    only5 = [x for x in fixed5 if x not in set(fixed6)]
    print(f"\n0005 returns tiling: {len(fixed5)} pairs, of which only exp-5 — {len(only5)}")
    print(f"0006 returns tiling: {len(fixed6)} pairs (B8G8R8A8 lists with UNORM/SRGB/SNORM/UINT/SINT)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default=DEFAULT_TABLE, help="path to fd6_format_table.c")
    ap.add_argument("--classes", default=os.path.join(REPO, "tests", "vkd3d-fmt-classes.h"))
    ap.add_argument("--all-pairs", action="store_true", help="enumerate all format pairs")
    ap.add_argument("--exp-9", action="store_true",
                    help="exp-9: a single-channel format does not push the list into linear")
    ap.add_argument("--exp-10", action="store_true",
                    help="exp-10: MUTABLEEN without UBWC in the !mutable_ubwc_fc branch")
    ap.add_argument("--blockers", action="store_true",
                    help="analysis of classes where the gate removes UBWC but tiling stays")
    ap.add_argument("--exp-0011", action="store_true",
                    help="exp-11: rows for the single-channel 16-bit "
                         "and packed-10-bit in fd6_ubwc_compat_mode()")
    ap.add_argument("--stack", action="store_true",
                    help="layout of the working stack and analysis of the remaining "
                         "tiled, no-UBWC classes")
    args = ap.parse_args()

    if not os.path.exists(args.table):
        sys.exit(f"no format table: {args.table}\n"
                 f"pass --table PATH (in the Mesa tree: src/freedreno/fdl/fd6_format_table.c)")

    swap, _ = load_format_table(args.table)
    print(f"GPU properties: FD740 = [a7xx_base, a7xx_gen2] -> {GEN2_PROPS}")
    if args.exp_9 or args.exp_10:
        print(f"experiments: exp-9={'on' if args.exp_9 else 'off'} "
              f"exp-10={'on' if args.exp_10 else 'off'}")
    print()

    if os.path.exists(args.classes):
        classes = load_vkd3d_classes(args.classes)
        report_vkd3d(swap, classes, GEN2_PROPS, args.exp_9, args.exp_10)
        if args.exp_0011:
            report_0011(swap, classes, GEN2_PROPS)
        if args.blockers:
            print("\n" + "=" * 78)
            report_blockers(swap, classes, GEN2_PROPS)
            print("\n" + "-" * 78 + "\nwith patch 0012 (_SFLOAT -> integer classes):")
            report_blockers(swap, classes, GEN2_PROPS, patch_0010=True)
            if args.exp_0011:
                print("\n" + "-" * 78 + "\nand with exp-11 (single-channel 16 bit "
                      "+ packed-10 bit):")
                report_blockers(swap, classes, GEN2_PROPS, patch_0010=True, patch_0011=True)
    if args.stack:
        report_stack(swap, classes, GEN2_PROPS, patch_0011=args.exp_0011)
    report_zink(swap, GEN2_PROPS)
    if args.all_pairs:
        report_all_pairs(swap, GEN2_PROPS)


if __name__ == "__main__":
    main()
