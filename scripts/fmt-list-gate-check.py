#!/usr/bin/env python3
"""Кто на самом деле попадает в linear при mutable-списке форматов.

Переносит на Python ровно те предикаты, которые решают судьбу образа в
`tu_image_init()` (src/freedreno/vulkan/tu_image.cc) и
`tu6_mutable_format_list_forces_linear()` (tu_formats.cc):

  tu6_mutable_format_list_ubwc_compatible()  -> ранний выход, весь блок пропускается
  fd6_ubwc_compat_mode()                     -> src/freedreno/common/freedreno_ubwc.h
  tu6_format_list_has_swaps()                -> swap из fd6_format_table.c, TILE6_LINEAR
  tu6_format_list_reinterprets_r8g8_r16()    -> blocksize / nr_components
  tu6_format_list_swaps_are_uniform()        -> патч 0005
  NV12-ветка                                 -> патч 0009 снимает force_linear_tile
  FD6_UBWC_B8G8R8A8_INT                      -> патч 0006

Эксперименты (нумерация experiments/ своя, флаги --exp-*):
  --exp-0009  одноканальный формат (R32_UINT и т.п.) не выводит список в
             linear: у него нет порядка каналов, о котором можно спорить.
             Меняет tu6_format_list_swaps_are_uniform().
  --exp-0010  в ветке !mutable_ubwc_fc выставляется is_mutable = true
             (MUTABLEEN без UBWC). Меняет только tu_image_init().

Ранний выход — главное: если список «UBWC-совместимый», до `has_swaps` дело
не доходит, и ни сток, ни патчи ничего не меняют. Перебор, который этот
ранний выход игнорирует, даёт неверные числа (так получилось в первом
разборе §16.2).

Свойства GPU берутся из freedreno_devices.py: FD740 = [a7xx_base, a7xx_gen2],
поэтому ubwc_unorm_snorm_int_compatible = True, ubwc_all_formats_compatible = 0.

Запуск:
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

# a7xx_gen2 (FD740) из src/freedreno/common/freedreno_devices.py.
# has_8bpp_ubwc = False наследуется от a6xx_gen2, supports_uav_ubwc = True стоит
# прямо в a7xx_gen2, то есть у UAV-образов гейт списка — действительно
# ограничивающий фактор, а у 8-битных на блок — нет.
GEN2_PROPS = {"ubwc_unorm_snorm_int_compatible": True, "ubwc_all_formats_compatible": False,
              "has_8bpp_ubwc": False, "supports_uav_ubwc": True}

# Формы блока, для которых в стоковом fd6_ubwc_compat_mode() есть строки:
# (число каналов, бит на канал). Взят из списка case заголовка.
SHAPES_IN_BLOB = {(2, 8), (4, 8), (2, 16), (4, 16), (1, 32), (2, 32), (4, 32)}

# Форматы, для которых UBWC невозможен независимо от гейта (tu6_ubwc_possible).
NO_UBWC_ALWAYS = ("BC", "ASTC", "ETC2", "EAC", "PVRTC", "G8_B8R8_2PLANE",
                  "G8_B8R8A8_2PLANE", "G16_B16R16_2PLANE_420", "G12B12G12B12_2PLANE_420",
                  "G10X6", "G16_B16R16_2PLANE_422", "G12B12G12B12_2PLANE_422",
                  "G24X8", "G24Y8", "R64_UINT", "R64_SINT", "R64G64", "E5B9G9R9_UFLOAT_PACK32",
                  "S8_UINT")


def is_compressed(f):
    """Блок-компрессия или планарное видео: UBWC для них невозможен по
    tu6_ubwc_possible() независимо от того, что решил гейт."""
    return norm(f).startswith(NO_UBWC_ALWAYS)


def why_unknown(f, props):
    """Почему fd6_ubwc_compat_mode() отдаёт UNKNOWN для этого формата.

    Это ключ к следующему рычагу: UNKNOWN означает «у blob-таблицы нет строки»,
    и потому НЕ исправляется никаким правилом в драйвере — нужно либо найти
    эквивалентный уже существующий класс, либо признать, что вопрос к железу.
    """
    f, g = norm(f), stem(f)
    if is_compressed(f):
        return "сжатый/плоский: UBWC невозможен"
    if re.fullmatch(r"R8(?:_[A-Z]+)?", g):
        return "8 бит на блок: has_8bpp_ubwc=False"
    if g in ("Z32_FLOAT", "Z24_UNORM_S8_UINT", "D24_UNORM_S8_UINT", "D32_SFLOAT",
             "X8_D24_UNORM_PACK32", "D16_UNORM"):
        return "глубина: отдельная семантика clear"
    if f in ("R64_UINT", "R64_SINT") or g.startswith("R64G64"):
        return "R64: UBWC запрещён"
    if g.startswith("R16_UINT") or g.startswith("R16_SINT") or g.startswith("R16_SNORM") \
            or g == "R16_UNORM":
        return "одноканальный 16 бит: строки в blob нет"
    if g.startswith("R10G10B10A2") or g.startswith("A2B10G10R10") \
            or g.startswith("A2R10G10B10") or g.startswith("R11G11B10") \
            or g.startswith("R5G6B5") or g.startswith("B5G6R5") or g.startswith("B5G5R5A1") \
            or g.startswith("B4G4R4A4") or g.startswith("R4G4B4A4"):
        return "packed-10/5/4 бит: строки в blob нет"
    if g.startswith("R32G32B32") and "A32" not in g:
        return "3-компонентный 32 бит: строки в blob нет"
    if re.fullmatch(r"R8G8B8_[A-Z]+", g) or re.fullmatch(r"B8G8R8_[A-Z]+", g):
        return "3-компонентный 8 бит: строки в blob нет"
    if re.fullmatch(r"R16G16B16_[A-Z]+", g) or re.fullmatch(r"B16G16R16_[A-Z]+", g):
        return "3-компонентный 16 бит: строки в blob нет"
    if g.startswith("R32_SFLOAT") or g.startswith("R32G32_FLOAT") \
            or g.startswith("R32G32B32A32_FLOAT") or g.startswith("R16G16_FLOAT") \
            or g.startswith("R16G16B16A16_FLOAT"):
        return "_SFLOAT вне целочисленных классов (эксп. 0008)"
    if g.startswith("B8G8R8A8") or g.startswith("B8G8R8X8"):
        return "B8G8R8A8-семейство вне UNORM-класса (патч 0006)"
    if re.fullmatch(r"[RGBA]\d+[RGBA]\d+[RGBA]\d+[RGBA]?\d*_[A-Z0-9_]+", g):
        return "нет строки в blob"
    return "нет строки в blob"


def load_format_table(path):
    """(pipe_format -> swap) для TILE6_LINEAR: третий аргумент _T_/VTC/TC."""
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
    """Перенос fd6_ubwc_compat_mode() из freedreno_ubwc.h (сток).

    patch_0010 — эксперимент: включить _SFLOAT-члены в целочисленные
    compat-классы.  Сток их не включает и для R32_FLOAT прямо пишет
    «a630 blob allows these, but not a660» (a660 == наше семейство gen2),
    так что это проверка гипотезы, а не перенос кода.
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
        # Одноканальный 16-битный и packed-10-битный.  Ни строки, ни даже
        # упоминания в fd6_format_table.c, но замер на 740 (results/test-CEIL-
        # stock.log, CEIL) показывает: одиночный образ R16_UNORM и
        # A2B10G10R10_UNORM_PACK32 БЕЗ списка получают метаданные UBWC, то
        # есть потолок есть, и его снимает именно гейт.  Все четыре варианта
        # одного размера блока (16 бит) различаются только интерпретацией.
        if fmt in ("R16_UNORM", "R16_SNORM", "R16_UINT", "R16_SINT",
                   "R16_FLOAT", "R16_SFLOAT"):
            return "R16_INT"
        # norm() срезает _PACK32, поэтому имена уже без суффикса
        if fmt in ("A2B10G10R10_UNORM", "A2B10G10R10_UINT",
                   "A2B10G10R10_SNORM", "R11G11B10_FLOAT"):
            return "A2B10G10R10_INT"

    if patch_0010:
        # каст внутри одного layout — те же биты, другое прочтение.
        # Z32_FLOAT НЕ трогаем: у него другая роль (depth), и сток
        # объединяет его с R32_FLOAT только чтобы пометить как UNKNOWN.
        # имена приходят и в pipe-виде (_FLOAT), и в вулканском (_SFLOAT)
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

    # семейство B8G8R8A8: сток — только UNORM/SRGB, стоковое B8G8R8A8_UNORM
    if fmt in ("B8G8R8A8_UNORM", "B8G8R8A8_SRGB"):
        return "B8G8R8A8_INT" if (patch_0006 and ok) else "B8G8R8A8_UNORM"
    if patch_0006:
        if fmt == "B8G8R8A8_SNORM":
            return "B8G8R8A8_INT" if ok else UNKNOWN
        if fmt in ("B8G8R8A8_UINT", "B8G8R8A8_SINT"):
            return "B8G8R8A8_INT"

    return UNKNOWN


def ubwc_compatible(formats, props, patch_0006, patch_0010=False, patch_0011=False):
    """tu6_mutable_format_list_ubwc_compatible(): True => весь блок пропускается."""
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
    """Разбивка списка по compat-режимам: [(режим, [форматы]), ...]."""
    groups = {}
    for f in formats:
        groups.setdefault(fd6_ubwc_compat_mode(f, props, patch_0006, patch_0010, patch_0011), []).append(f)
    return sorted(groups.items(), key=lambda kv: (kv[0] == UNKNOWN, kv[0]))


def blockers(formats, props, patch_0006=False, patch_0010=False, patch_0011=False):
    """Сигнатура того, что ломает гейт, плюс сами блокирующие пары.

    Сигнатура — отсортированный список (режим, первый формат этого режима).
    Форматы с UNKNOWN выносятся в конец: для них blob-таблицы a630/a660 не
    содержит строки вообще, поэтому никакое расширение правил их не закроет.
    """
    groups = ubwc_groups(formats, props, patch_0006, patch_0010, patch_0011)
    sig = tuple((mode, fmts[0]) for mode, fmts in groups)
    pairs = []
    for i, (m1, f1s) in enumerate(groups):
        for m2, f2s in groups[i + 1:]:
            pairs.append((f1s[0], m1, f2s[0], m2))
    return sig, pairs, groups


def report_blockers(swap, classes, props, patch_0006=False, patch_0010=False, patch_0011=False):
    """Разбор классов, у которых гейт снимает UBWC, но тайлинг остаётся."""
    rows = []
    for name, fmts in classes:
        if ubwc_compatible(fmts, props, patch_0006, patch_0010, patch_0011):
            continue
        sig, pairs, groups = blockers(fmts, props, patch_0006, patch_0010, patch_0011)
        rows.append((name, fmts, sig, pairs, groups))
    if not rows:
        print("\n### Блокеры UBWC: таких классов нет\n")
        return
    print(f"\n### Что блокирует снятие UBWC: {len(rows)} классов\n")
    print("A. Не блокер гейта: формату UBWC невозможен в принципе "
          "(tu6_ubwc_possible()).\n")
    hard = [r for r in rows if any(is_compressed(f) for f in r[1])]
    for name, fmts, sig, pairs, groups in hard:
        base = fmts[0]
        print(f"  [{len(fmts)}] {name:26} базовый={base:16} {why_unknown(base, props)}")
    print(f"  итого недостижимо: {len(hard)} классов из {len(rows)}\n")

    print("B. Блокер — сам гейт: список уходит в режим без UBWC, хотя каждый "
          "формат по отдельности\n   UBWC держит. Это единственная группа, где "
          "правка правила имеет смысл.\n")
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
            print(f"          {name:26} n={len(fmts):2}  причина UNKNOWN: {why}")
    print(f"\n  итого блокером гейта: {len(soft)} из {len(rows)} классов")
    return rows


def report_0011(swap, classes, props):
    """Что меняет эксперимент 0011: строки для R16_* и A2B10G10R10_*.

    Основание — замер, а не рассуждение.  Проба CEIL (results/test-CEIL-
    stock.log) показала, что одиночный R16_UNORM и A2B10G10R10_UNORM_PACK32 без
    списка получают метаданные UBWC: потолок есть.  Список их снимает, потому
    что fd6_ubwc_compat_mode() для них молчит.  В отличие от 0008 здесь не
    нужно утверждать, что blob «allows but not a660»: форматы в blob-таблице не
    упомянуты вообще, и A2B10G10R10 нет даже в fd6_format_table.c.
    """
    closed = []
    for name, fmts in classes:
        if ubwc_compatible(fmts, props, False, patch_0010=True):
            continue
        if not ubwc_compatible(fmts, props, False, patch_0010=True, patch_0011=True):
            continue
        # tu_image_init() отдаёт кортеж (Layout, сработал ли ранний выход), а
        # вердикт здесь — про UBWC, то есть про ранний выход.
        _, early_before = tu_image_init(fmts[0], fmts, swap, props, patch_0010=True)
        _, early_after = tu_image_init(fmts[0], fmts, swap, props, patch_0010=True,
                                       patch_0011=True)
        closed.append((name, fmts, "UBWC снят гейтом" if not early_before else "UBWC",
                       "tiled+UBWC" if early_after else "??"))
    print(f"\n### Эксперимент 0011: закрывает {len(closed)} классов\n")
    for name, fmts, before, after in closed:
        print(f"  {name:24} n={len(fmts)}  {before}  ->  {after}")
    return closed


def report_stack(swap, classes, props, patch_0011=False):
    """Раскладка рабочего стека ArmadOS и разбор оставшихся tiled, no-UBWC.

    Рабочий стек — 0004, 0005, 0006, 0008 (_SFLOAT), 0009, 0010.  0004 и 0007 на
    раскладку не влияют: 0004 запрещает sparse, 0007 — только диагностика.

    Группы порядят НЕ по префиксу имени.  Первая версия этого разбора так и
    делала, и `G16_B16R16_2PLANE_420_UNORM` уехал в группу `R16_*`, а
    `G8_B8R8_2PLANE_420_UNORM` — в `R8_*`, потому что `R16_` и `R8_` в них
    встречаются как подстрока.  Проверка на сжатие идёт первой, форматы
    плоского видео отсеиваются ею же.
    """
    kw = dict(patch_0005=True, patch_0006=True, patch_0010=True, exp_0009=True)
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
            fam = "сжатые и планарные: ubwc_possible() = false"
        elif any(norm(f).startswith("R8_") for f in fmts):
            fam = "R8_*: has_8bpp_ubwc = false, потолка нет"
        elif any("R32G32B32" in norm(f) for f in fmts):
            fam = "R32G32B32_*: потолка нет"
        elif any(norm(f).startswith(("R16_", "A2B10G10R10", "R10G10B10A2"))
                 for f in fmts):
            fam = "R16_* / R10G10B10A2_*: потолок есть, снимает гейт"
        else:
            # остаётся случай двух compat-классов в одном списке: vkd3d сам
            # дописывает R32{U,I,F} ради typed UAV load
            fam = "два compat-класса в одном списке (B8G8R8A8_UNORM + R32_*)"
        groups.setdefault(fam, []).append(name)

    rest = sum(len(v) for v in groups.values())
    print(f"\n### Рабочий стек (0004, 0005, 0006, 0008, 0009, 0010"
          f"{', 0011' if patch_0011 else ''}): из {len(classes)} классов")
    print(f"  UBWC: {n_ubwc}   linear: {n_lin}   tiled, no-UBWC: {rest}\n")
    for tag, lst in sorted(groups.items(), key=lambda x: -len(x[1])):
        print(f"  [{len(lst):2}] {tag}")
        print(f"        {', '.join(lst)}")
    return groups


def geom(fmt):
    """(байт на тексель, число каналов) — для tu_is_r8g8*."""
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
    """Каналы в порядке памяти: R8G8B8A8 и B8G8R8A8 тут различаются."""
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


def swaps_are_uniform(formats, swap, exp_0009=False):
    """tu6_format_list_swaps_are_uniform() из патча 0005.

    exp_0009 — эксперимент 0009: формат с одним каналом не имеет порядка
    каналов, поэтому его собственный swap не может поспорить с чужим; от него
    требуется только совпадение формы блока (bits/width/height), потому что
    раскладка тайла — функция размера блока (fd6_layout.c: layout->cpp).
    """
    if not formats or len(formats) < 2:
        return False
    if exp_0009:
        # форма блока — это блок по размеру, а не вместе с числом каналов:
        # раскладка тайла зависит только от util_format_get_blocksize()
        block = lambda f: geom(f)[0]
        multi = [f for f in formats if len(channels(f)) > 1]
        if not multi:
            # весь список одноканальный: спорить не о чем
            return len({block(f) for f in formats}) == 1
        for f in formats:
            if block(f) != block(multi[0]):
                return False
            if len(channels(f)) < 2:
                continue   # порядка каналов нет — спорить не о чем
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
    """Что решил драйвер для образа: тайлинг, UBWC и бит MUTABLEEN."""

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
                  exp_0009=False, exp_0010=False):
    """Возвращает (Layout, сработал ли ранний выход)."""
    if ubwc_compatible(formats, props, patch_0006, patch_0010, patch_0011):
        return Layout(True, True, "ранний выход: список UBWC-совместим"), True

    if base == NV12_VK:
        # сток: force_linear_tile = true; патч 0009 оставляет тайлинг
        return Layout(False, patch_0009, "NV12"), False

    if props["ubwc_all_formats_compatible"]:
        return Layout(True, True, "ubwc_all_formats_compatible"), False

    tiled = True
    branch = "только снятие UBWC"
    if reinterprets_r8g8_r16(base, formats):
        tiled, branch = False, "r8g8↔r16"
    elif has_swaps(formats, swap) and (not patch_0005 or
                                       not swaps_are_uniform(formats, swap, exp_0009)):
        tiled, branch = False, "has_swaps" + ("" if patch_0005 else " (сток)")
    # эксп. 0010 ставит is_mutable = true в этой же ветке, где UBWC уже снят.
    # Тайлинг он не трогает, а на ген2 при расходящихся swap'ах только ломает
    # содержимое (docs/analysis.md §21.4), поэтому в модели он отмечен флагом,
    # а не подменой раскладки.
    return Layout(False, tiled, branch, mutable=exp_0010), False


def forces_linear(base, formats, swap, props, patch_0005=False, patch_0006=False, exp_0009=False):
    """tu6_mutable_format_list_forces_linear(): sparse-запрос из патча 0004."""
    if ubwc_compatible(formats, props, patch_0006):
        return False, "ранний выход"
    if re.fullmatch(r"D16|D24|D32|S8|X8_D24", base):
        return False, "depth/stencil"
    if base == NV12_VK:
        return True, "NV12"
    if props["ubwc_all_formats_compatible"]:
        return False, "ubwc_all_formats_compatible"
    if reinterprets_r8g8_r16(base, formats):
        return True, "r8g8↔r16"
    if has_swaps(formats, swap) and (not patch_0005 or
                                    not swaps_are_uniform(formats, swap, exp_0009)):
        return True, "has_swaps" + ("" if patch_0005 else " (сток)")
    return False, "нет триггера"


def load_vkd3d_classes(path):
    src = open(path).read()
    out = []
    for name, body in re.findall(r'\{\s*"([^"]+)"\s*,\s*\{([^}]*)\}', src):
        fmts = [x.strip().replace("VK_FORMAT_", "") for x in body.split(",") if x.strip()]
        out.append((name, fmts))
    return out


def norm(fmt):
    """Имя из таблицы pipe-форматов: убрать вулкан'sкие _PACK16/_PACK32."""
    return re.sub(r"_PACK(16|32)$", "", fmt)


INTYPES = ("_UNORM", "_SNORM", "_UINT", "_SINT", "_SFLOAT", "_SRGB")


def stem(fmt):
    """Имя каналов без хвоста интерпретации: A8R8G8B8_UNORM_SRGB -> A8R8G8B8."""
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
    """Пары, которые строит setup_format_list(): формат + его sRGB-вариант.

    Zink: srgb = util_format_srgb(templ->format), и в список идут ровно эти
    два формата. util_format_linear() убирает хвост "_SRGB" (либо добавляет
    "_UNORM", если хвоста не было), так что пара всегда (F, F_SRGB) с одним
    и тем же порядком каналов.
    """
    pairs = []
    for fmt in sorted(swap):
        if not fmt.endswith("_SRGB"):
            continue
        linear = stem(fmt) + "_UNORM"
        if table_lookup(swap, linear):
            pairs.append((linear, fmt))
    return pairs


def report_vkd3d(swap, classes, props, exp_0009=False, exp_0010=False):
    print(f"### vkd3d: {len(classes)} классов из tests/vkd3d-fmt-classes.h\n")
    groups = {}
    for name, fmts in classes:
        base = fmts[0]
        stock, early = tu_image_init(base, fmts, swap, props)
        p5, _ = tu_image_init(base, fmts, swap, props, patch_0005=True)
        p56, _ = tu_image_init(base, fmts, swap, props, patch_0005=True, patch_0006=True)
        p9 = tu_image_init(base, fmts, swap, props, patch_0005=True, exp_0009=exp_0009)[0] \
            if exp_0009 else None
        p10 = tu_image_init(base, fmts, swap, props, patch_0005=True,
                            exp_0009=exp_0009, exp_0010=exp_0010)[0] if exp_0010 else None
        key = (early, str(stock), str(p5), str(p56), str(p9) if p9 else "",
               str(p10) if p10 else "", stock.branch)
        groups.setdefault(key, []).append(name)
    for (early, a, b, c, d, e, branch), names in sorted(groups.items(), key=lambda x: -len(x[1])):
        head = "ранний выход, " if early else ""
        tail = f" -> +эксп.0009 {d}" if d else ""
        tail += f" -> +эксп.0010 {e}" if e else ""
        print(f"[{len(names):3}] {head}{a} -> +0005 {b} -> +0005+0006 {c}{tail}   ({branch})")
        if len(names) <= 6:
            print(f"        {', '.join(names)}")
    linear = [n for n, f in classes if not tu_image_init(f[0], f, swap, props)[0].tiled]
    print(f"\nв linear на стоке: {len(linear)} из {len(classes)}")
    if exp_0009:
        # что именно переворачивает эксперимент 0009
        flipped = []
        for name, fmts in classes:
            base = fmts[0]
            a = tu_image_init(base, fmts, swap, props, patch_0005=True)[0]
            b = tu_image_init(base, fmts, swap, props, patch_0005=True, exp_0009=True)[0]
            if a.tiled != b.tiled:
                flipped.append((name, str(a), str(b)))
        print(f"эксп.0009 переворачивает {len(flipped)} классов:")
        for name, a, b in flipped:
            print(f"        {name}: {a} -> {b}")
        left = [n for n, f in classes
                if not tu_image_init(f[0], f, swap, props, patch_0005=True, exp_0009=True)[0].tiled]
        print(f"в linear с эксп.0009: {len(left)} из {len(classes)} -> {', '.join(left)}")
    return linear


def report_zink(swap, props):
    pairs = zink_srgb_pairs(swap)
    print(f"\n### Zink: setup_format_list() строит {len(pairs)} пар «формат + sRGB-вариант»\n")
    buckets = {}
    for a, b in pairs:
        base = a
        stock, early = tu_image_init(base, [a, b], swap, props)
        p5, _ = tu_image_init(base, [a, b], swap, props, patch_0005=True)
        p56, _ = tu_image_init(base, [a, b], swap, props, patch_0005=True, patch_0006=True)
        key = (early, str(stock), str(p5), str(p56))
        buckets.setdefault(key, []).append(f"{a} + {b}")
    for key, lst in sorted(buckets.items(), key=lambda x: -len(x[1])):
        early = "ранний выход, " if key[0] else ""
        print(f"[{len(lst):3}] {early}{key[1]} -> +0005 {key[2]} -> +0005+0006 {key[3]}")
        for e in lst[:5]:
            print(f"        {e}")
        if len(lst) > 5:
            print(f"        … ещё {len(lst) - 5}")

    print("\n### Zink: прочие случаи\n")
    cases = [
        ("ZINK_BIND_MUTABLE: MUTABLE без списка (init_ici обнуляет pNext)", "R8G8B8A8_UNORM", None),
        ("видео NV12: плоскости одного формата", NV12_VK, [NV12_VK, "R8_UNORM", "R8G8_UNORM"]),
        ("кросс-порядок {BGRA8, RGBA8}", "R8G8B8A8_UNORM", ["B8G8R8A8_UNORM", "R8G8B8A8_UNORM"]),
        ("uniform-swap {BGRA8_UNORM, BGRA8_UINT}", "B8G8R8A8_UNORM", ["B8G8R8A8_UNORM", "B8G8R8A8_UINT"]),
        ("BGR-семейство {B5G6R5, B5G5R5A1}", "B5G6R5_UNORM", ["B5G6R5_UNORM", "B5G5R5A1_UNORM"]),
    ]
    for name, base, fmts in cases:
        stock, _ = tu_image_init(base, fmts, swap, props)
        p5, _ = tu_image_init(base, fmts, swap, props, patch_0005=True)
        p6, _ = tu_image_init(base, fmts, swap, props, patch_0006=True)
        p56, _ = tu_image_init(base, fmts, swap, props, patch_0005=True, patch_0006=True)
        p59, _ = tu_image_init(base, fmts, swap, props, patch_0005=True, exp_0009=True)
        fl_stock, _ = forces_linear(base, fmts, swap, props)
        fl_p5, _ = forces_linear(base, fmts, swap, props, patch_0005=True)
        fl_p59, _ = forces_linear(base, fmts, swap, props, patch_0005=True, exp_0009=True)
        print(f"{name}\n    create:   сток {stock:16} +0005 {p5:16} +0006 {p6:16} +0005+0006 {p56:16}"
              f"\n    эксп.0009: {p59:16}"
              f"\n    sparse:   сток forces_linear={fl_stock!s:5} +0005 {fl_p5!s:5} +эксп.0009 {fl_p59!s:5}")
    print("\n    ZINK_BIND_MUTABLE создаётся, когда zink_format_needs_mutable() истинно:")
    print("    пары x8/alpha-эмуляции дают false, то есть mutable не нужен; значит список")
    print("    либо совместим (sRGB), либо отсутствует вовсе.")


def report_all_pairs(swap, props):
    fmts = sorted(swap)
    print(f"\n### Все {len(fmts)}x{len(fmts) - 1} упорядоченных пар форматов из fd6_format_table.c\n")
    buckets = {}
    for a, b in itertools.permutations(fmts, 2):
        pair = [a, b]
        stock, early = tu_image_init(a, pair, swap, props)
        p5, _ = tu_image_init(a, pair, swap, props, patch_0005=True)
        p6, _ = tu_image_init(a, pair, swap, props, patch_0006=True)
        if early:
            kind = "ранний выход (tiled+UBWC)"
        elif not has_swaps(pair, swap):
            kind = "swap'ов нет: UBWC снимается, тайлинг остаётся — патчи ни при чём"
        elif swaps_are_uniform(pair, swap):
            kind = "uniform-swap: сток linear -> +0005 tiled -> +0006 %s" % ("linear" if not p6.tiled else "tiled+UBWC")
        else:
            kind = "кросс-порядок: linear у всех (эксперимент cross-order)"
        buckets.setdefault(kind, []).append((a, b, stock.tiled, p5.tiled, p6.tiled))
    for kind, lst in sorted(buckets.items(), key=lambda x: -len(x[1])):
        print(f"[{len(lst):6}] {kind}")
        for e in lst[:6]:
            print(f"          {e[0]} + {e[1]}  (tiled: сток={e[2]} 0005={e[3]} 0006={e[4]})")
        if len(lst) > 6:
            print(f"          … ещё {len(lst) - 6}")
    fixed5 = [(a, b) for lst in buckets.values() for a, b, s, p5, _ in lst if s == False and p5]
    fixed6 = [(a, b) for lst in buckets.values() for a, b, s, _, p6 in lst if s == False and p6]
    only5 = [x for x in fixed5 if x not in set(fixed6)]
    print(f"\n0005 возвращает тайлинг: {len(fixed5)} пар, из них только 0005 — {len(only5)}")
    print(f"0006 возвращает тайлинг: {len(fixed6)} пар (списки B8G8R8A8 с UNORM/SRGB/SNORM/UINT/SINT)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default=DEFAULT_TABLE, help="путь к fd6_format_table.c")
    ap.add_argument("--classes", default=os.path.join(REPO, "tests", "vkd3d-fmt-classes.h"))
    ap.add_argument("--all-pairs", action="store_true", help="перебрать все пары форматов")
    ap.add_argument("--exp-0009", action="store_true",
                    help="эксперимент 0009: одноканальный формат не выводит список в linear")
    ap.add_argument("--exp-0010", action="store_true",
                    help="эксперимент 0010: MUTABLEEN без UBWC в ветке !mutable_ubwc_fc")
    ap.add_argument("--blockers", action="store_true",
                    help="разбор классов, где гейт снимает UBWC, но тайлинг остаётся")
    ap.add_argument("--exp-0011", action="store_true",
                    help="эксперимент 0011: строки для одноканального 16-битного "
                         "и packed-10-битного в fd6_ubwc_compat_mode()")
    ap.add_argument("--stack", action="store_true",
                    help="раскладка рабочего стека и разбор оставшихся "
                         "tiled, no-UBWC классов")
    args = ap.parse_args()

    if not os.path.exists(args.table):
        sys.exit(f"нет таблицы форматов: {args.table}\n"
                 f"укажи --table PATH (в дереве Mesa: src/freedreno/fdl/fd6_format_table.c)")

    swap, _ = load_format_table(args.table)
    print(f"свойства GPU: FD740 = [a7xx_base, a7xx_gen2] -> {GEN2_PROPS}")
    if args.exp_0009 or args.exp_0010:
        print(f"эксперименты: 0009={'вкл' if args.exp_0009 else 'выкл'} "
              f"0010={'вкл' if args.exp_0010 else 'выкл'}")
    print()

    if os.path.exists(args.classes):
        classes = load_vkd3d_classes(args.classes)
        report_vkd3d(swap, classes, GEN2_PROPS, args.exp_0009, args.exp_0010)
        if args.exp_0011:
            report_0011(swap, classes, GEN2_PROPS)
        if args.blockers:
            print("\n" + "=" * 78)
            report_blockers(swap, classes, GEN2_PROPS)
            print("\n" + "-" * 78 + "\nс поправкой эксп. 0008 (_SFLOAT -> целочисленные классы):")
            report_blockers(swap, classes, GEN2_PROPS, patch_0010=True)
            if args.exp_0011:
                print("\n" + "-" * 78 + "\nи с экспериментом 0011 (одноканальный 16 бит "
                      "+ packed-10 бит):")
                report_blockers(swap, classes, GEN2_PROPS, patch_0010=True, patch_0011=True)
    if args.stack:
        report_stack(swap, classes, GEN2_PROPS, patch_0011=args.exp_0011)
    report_zink(swap, GEN2_PROPS)
    if args.all_pairs:
        report_all_pairs(swap, GEN2_PROPS)


if __name__ == "__main__":
    main()
