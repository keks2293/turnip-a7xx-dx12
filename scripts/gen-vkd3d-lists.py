#!/usr/bin/env python3
"""Сгенерировать C-массив списков совместимости форматов из исходников vkd3d-proton.

Источники (обе таблицы статические, поэтому перебор точен по построению):
  libs/vkd3d/utils.c: vkd3d_formats[]                  - карта DXGI -> VkFormat
  libs/vkd3d/utils.c: dxgi_format_compatibility_list[] - списки для mutable-образов

Проба нужна для одного вопроса: sparse-ресурс в vkd3d - это ровно
CreateReservedResource (resource.c: heap_properties = RESERVED ? NULL : &resource->heap_properties,
поэтому sparse_resource = !heap_properties истинно только для reserved). Для reserved
ресурса vkd3d строит список из format_compatibility_lists[Format] и ставит
MUTABLE_FORMAT_BIT, если в списке >= 2 форматов (resource.c:
vkd3d_get_format_compatibility_list, "if (list->format_count < 2) return false;").
То есть MUTABLE + список в связке, и вопрос сводится к одному: какой из этих
списков драйвер turnip примет вместе с SPARSE_RESIDENCY.

Использование: gen-vkd3d-lists.py /path/to/vkd3d-proton > lists.h
"""
import re
import sys

VK_FMT = re.compile(r"VK_FORMAT_[A-Z0-9_]+")


def parse_format_map(text):
    """{DXGI_FORMAT_X: VK_FORMAT_Y} из таблицы vkd3d_formats[]."""
    out = {}
    for m in re.finditer(r"\{(DXGI_FORMAT_[A-Z0-9_]+),\s*(VK_FORMAT_[A-Z0-9_]+)", text):
        out.setdefault(m.group(1), m.group(2))
    return out


def parse_compat_lists(text, fmap):
    """Классы с >= 2 форматами: [(dxgi, [vk, ...]), ...] в порядке таблицы."""
    body = text[text.index("dxgi_format_compatibility_list[] ="):]
    body = body[: body.index("\n};")]
    # запись: {DXGI_FORMAT_X,\n {A, B, C},\n ...} - первое поле image_format,
    # второе - список view_formats, остальные (uint/typeless) не интересуют.
    entries = []
    for m in re.finditer(
        r"\{(DXGI_FORMAT_[A-Z0-9_]+),\s*\{([^}]*)\}", body
    ):
        dxgi, views = m.group(1), m.group(2)
        dxf = [v.strip() for v in views.split(",") if v.strip() and "UNKNOWN" not in v]
        # список = сам image_format + все view_formats (resource.c: add_format(image) затем цикл)
        full = [dxgi] + dxf
        vks, seen = [], set()
        for d in full:
            vk = fmap.get(d)
            if vk and vk not in seen:
                seen.add(vk)
                vks.append(vk)
        if len(vks) >= 2:  # иначе MUTABLE не ставится вовсе
            entries.append((dxgi, vks))
    return entries


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "/var/home/armada/opencode/srcs/vkd3d-proton"
    text = open(root + "/libs/vkd3d/utils.c", encoding="utf-8", errors="replace").read()
    fmap = parse_format_map(text)
    entries = parse_compat_lists(text, fmap)

    cap = 8
    trunc = [d for d, v in entries if len(v) > cap]
    if trunc:
        # Молча обрезать нельзя: потерянный формат сделал бы вывод ложным.
        sys.exit("Список длиннее %d в: %s" % (cap, ", ".join(trunc)))

    print("/* Сгенерировано scripts/gen-vkd3d-lists.py из %s - не править руками. */" % root)
    print("#define VKD3D_MUTABLE_CLASS_COUNT %d" % len(entries))
    print("struct vkd3d_fmt_class { const char *dxgi; VkFormat f[%d]; unsigned int n; };" % cap)
    print("static const struct vkd3d_fmt_class vkd3d_fmt_classes[] = {")
    for dxgi, vks in entries:
        print('    { "%s", { %s }, %d },'
              % (dxgi.replace("DXGI_FORMAT_", ""), ", ".join(vks), len(vks)))
    print("};")
    sys.stderr.write("классов с >=2 форматами: %d (макс. длина списка %d)\n"
                     % (len(entries), max(len(v) for _, v in entries)))


if __name__ == "__main__":
    main()
