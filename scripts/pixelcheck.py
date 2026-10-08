#!/usr/bin/env python3
"""Проверка корректности флага TU_FORCE_PROPS по снимкам экрана.

Сцены vkmark анимированы, поэтому сравнивать кадры целиком нельзя: шум между
двумя одинаковыми прогонами базы (5-8% пикселей) больше эффекта флага.
Вместо этого берём точки экрана, совпавшие между двумя прогонами базы, — это
неподвижный фон. Если флаг ломает clear/загрузку буфера, фон в этих точках
изменится, а анимация мешать не будет.

Запуск: pixelcheck.py base1.png base2.png flag.png
"""
import sys

from PIL import Image


def grid_points(w, h, step=64):
    pts = []
    y = step // 2
    while y < h:
        x = step // 2
        while x < w:
            pts.append((x, y))
            x += step
        y += step
    return pts


def main(base1_path, base2_path, flag_path):
    b1 = Image.open(base1_path).convert("RGB")
    b2 = Image.open(base2_path).convert("RGB")
    fl = Image.open(flag_path).convert("RGB")
    if not (b1.size == b2.size == fl.size):
        print("размеры кадров различаются: %s %s %s"
              % (b1.size, b2.size, fl.size), file=sys.stderr)
        return 1

    pts = grid_points(*b1.size)
    stable = [p for p in pts if b1.getpixel(p) == b2.getpixel(p)]
    moved = [p for p in pts if b1.getpixel(p) != b2.getpixel(p)]

    bad = [p for p in stable if b1.getpixel(p) != fl.getpixel(p)]
    # близкие по значению отличия (сглаживание/композитор) считаем допустимыми
    near = [p for p in bad
            if max(abs(a - b) for a, b in zip(b1.getpixel(p), fl.getpixel(p))) <= 4]
    really_bad = [p for p in bad if p not in near]

    print("точек в сетке:      %d" % len(pts))
    print("стабильных (база-база совпали): %d" % len(stable))
    print("движущихся (анимация):         %d" % len(moved))
    print("изменений на флаге среди стабильных: %d (из них с отклонением >4: %d)"
          % (len(bad), len(really_bad)))
    for p in really_bad[:10]:
        print("   точка %s: база %s -> флаг %s"
              % (p, b1.getpixel(p), fl.getpixel(p)))
    if not really_bad:
        print("ВЕРДИКТ: фон идентичен — порчи в стабильных областях нет")
        return 0
    print("ВЕРДИКТ: есть отличающиеся стабильные точки — нужен разбор")
    return 2


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
