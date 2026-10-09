#!/usr/bin/env python3
"""Check of the TU_FORCE_PROPS flag's correctness via screenshots.

vkmark scenes are animated, so whole frames cannot be compared: the noise between
two identical baseline runs (5-8% of pixels) is larger than the flag's effect.
Instead we take screen points that matched between two baseline runs, — that is
the static background. If the flag breaks clear/buffer upload, the background at
these points will change, and the animation will not get in the way.

Run: pixelcheck.py base1.png base2.png flag.png
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
        print("frame sizes differ: %s %s %s"
              % (b1.size, b2.size, fl.size), file=sys.stderr)
        return 1

    pts = grid_points(*b1.size)
    stable = [p for p in pts if b1.getpixel(p) == b2.getpixel(p)]
    moved = [p for p in pts if b1.getpixel(p) != b2.getpixel(p)]

    bad = [p for p in stable if b1.getpixel(p) != fl.getpixel(p)]
    # differences that are close in value (antialiasing/compositor) are considered acceptable
    near = [p for p in bad
            if max(abs(a - b) for a, b in zip(b1.getpixel(p), fl.getpixel(p))) <= 4]
    really_bad = [p for p in bad if p not in near]

    print("points in the grid:      %d" % len(pts))
    print("stable (baseline-baseline matched): %d" % len(stable))
    print("moving (animation):                 %d" % len(moved))
    print("changes on the flag among stable: %d (of those with deviation >4: %d)"
          % (len(bad), len(really_bad)))
    for p in really_bad[:10]:
        print("   point %s: baseline %s -> flag %s"
              % (p, b1.getpixel(p), fl.getpixel(p)))
    if not really_bad:
        print("VERDICT: background identical — no corruption in stable areas")
        return 0
    print("VERDICT: there are differing stable points — needs analysis")
    return 2


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
