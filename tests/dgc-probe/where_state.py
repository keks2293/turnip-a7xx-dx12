#!/usr/bin/env python3
"""where_state.py file.rd — полный разбор состояния draw-пакета:
 SDS-группы draw-state (имена регистров + значения) + main-стрим (marker/режим).
"""
import gzip, struct, sys, re

def _open(path):
    with open(path, 'rb') as f:
        magic = f.read(2)
    return gzip.open(path, 'rb') if magic == b'\x1f\x8b' else open(path, 'rb')

def parse(path):
    data = _open(path).read()
    off, bos, addrs, contents = 0, [], [], []
    while off + 8 <= len(data):
        t, sz = struct.unpack_from('<II', data, off)
        off += 8
        body = data[off:off + sz]
        off += sz
        if t == 3 and len(body) >= 12:
            lo, size, hi = struct.unpack_from('<III', body)
            bos.append(((hi << 32) | lo, size))
        elif t == 6 and len(body) >= 12:
            lo, ndw, hi = struct.unpack_from('<III', body)
            addrs.append(((hi << 32) | lo, ndw))
        elif t == 12:
            contents.append((bos[-1][0] if bos else 0, body))
    return addrs, contents

def dws_of(body):
    n = len(body) // 4
    return list(struct.unpack('<%dI' % n, body[:n * 4]))

XML = '/var/home/armada/opencode/build/mesa-rp6-dgc/src/freedreno/registers/adreno/a6xx.xml'
def load_names():
    txt = open(XML).read()
    names = {}
    for m in re.finditer(r'<(reg32|reg64|reg16|reg96)\s+([^>]*)/?>', txt):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(2)))
        if 'offset' in attrs and 'name' in attrs:
            names[int(attrs['offset'], 16)] = attrs['name']
    for m in re.finditer(r'<array\s+([^>]*)>', txt):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        if not all(k in attrs for k in ('offset', 'name', 'stride')):
            continue
        off, stride = int(attrs['offset'], 16), int(attrs['stride'])
        length = int(attrs.get('length', 8))
        for i in range(min(length, 32)):
            names.setdefault(off + i * stride,
                              '%s[%d]' % (attrs['name'], i))
    return names

GROUP = ['PROGRAM_CONFIG', 'VS', 'VS_BINNING', 'HS', 'DS', 'GS', 'GS_BINNING',
         'VPC', 'FS', 'VB', 'CONST', 'DESC_SETS', 'DESC_SETS_LOAD',
         'VS_PARAMS', 'FS_PARAMS', 'INPUT_ATTACHMENTS_GMEM',
         'INPUT_ATTACHMENTS_SYSMEM', 'LRZ_AND_DEPTH_PLANE', 'PRIM_MODE_GMEM']
def gname(g):
    return GROUP[g] if g < len(GROUP) else 'DYNAMIC+%d' % (g - 19) \
        if 19 <= g < 19 + 20 else 'G%d' % g

def decode_blob(dws, names, depth=0):
    """mini-PM4: T7-bunch(0x5c), T4(0x4xxxxxxx), прочее -> (reg,val)."""
    out, i = [], 0
    while i < len(dws):
        dw = dws[i]
        if dw == 0:
            i += 1
            continue
        if (dw >> 29) == 0b011:
            op, cnt = (dw >> 16) & 0x7f, dw & 0x3fff
            if op == 0x5c:                      # CONTEXT_REG_BUNCH
                p = dws[i + 1:i + 1 + cnt]
                for j in range(0, len(p) - 1, 2):
                    out.append((p[j] & 0xffff, p[j + 1]))
                i += 1 + cnt
                continue
            out.append(('T7op%02x' % op, dws[i + 1:i + 1 + cnt]))
            i += 1 + cnt
            continue
        if (dw >> 30) == 0b01:                  # T4
            reg, cnt = (dw >> 8) & 0x3ffff, dw & 0x7f
            p = dws[i + 1:i + 1 + cnt]
            if cnt == 1:
                out.append((reg, p[0] if p else None))
            else:
                for j, v in enumerate(p):
                    out.append((reg + j, v))
            i += 1 + cnt
            continue
        out.append(('dw%08x' % dw, None))
        i += 1
    return out

def fmt_reg(reg, names):
    if isinstance(reg, str):
        return reg
    nm = names.get(reg, '??')
    return '0x%04x %-34s' % (reg, nm)

def main():
    addrs, contents = parse(sys.argv[1])
    names = load_names()
    pool = None
    draw_cs = None
    for iova, body in contents:
        d = dws_of(body)
        if any(x in (0x57480000, 0x57480001, 0x57480002) for x in d):
            draw_cs = (iova, d)
        if iova == 0x100005000:
            pool = d
    print('== main stream:')
    for a, ndw in addrs:
        for iova, body in contents:
            if iova == a:
                md = dws_of(body)[:ndw]
                # поиск SET_MARKER(0x45) и прочих T7
                i = 0
                while i < len(md):
                    dw = md[i]
                    if (dw >> 29) == 0b011:
                        op, cnt = (dw >> 16) & 0x7f, dw & 0x3fff
                        if op in (0x45, 0x47, 0x21, 0x60, 0x64):
                            print('   %5d: T7 op=0x%02x cnt=%d payload=%s'
                                  % (i, op, cnt,
                                     ' '.join('%08x' % x for x in md[i+1:i+1+cnt])))
                        i += 1 + cnt
                    elif (dw >> 30) == 0b01:
                        i += 1 + (dw & 0x7f)
                    elif (dw >> 31) and not (dw >> 30) & 1:
                        i += 2
                    else:
                        i += 1 + (((dw >> 16) & 0x3fff) + 1)
    if not draw_cs:
        print('draw_cs not found', file=sys.stderr)
        sys.exit(1)
    _, dcs = draw_cs
    # найти все SET_DRAW_STATE в draw_cs
    i = 0
    while i < len(dcs):
        dw = dws[i] if False else dcs[i]
        if (dw >> 29) == 0b011:
            op, cnt = (dw >> 16) & 0x7f, dw & 0x3fff
            if op == 0x43:
                print('== SET_DRAW_STATE @dw%d cnt=%d' % (i, cnt))
                p = dcs[i + 1:i + 1 + cnt]
                for j in range(0, cnt, 3):
                    ctrl, lo, hi = p[j], p[j + 1], p[j + 2]
                    gid, sz = (ctrl >> 24) & 0x3f, ctrl & 0xffff
                    dis = (ctrl >> 17) & 1
                    modes = 'B%d G%d S%d' % ((ctrl >> 20) & 1,
                                             (ctrl >> 21) & 1, (ctrl >> 22) & 1)
                    iova = lo | (hi << 32)
                    print('  grp=%-26s cnt=%3d %s %s iova=0x%x'
                          % (gname(gid), sz, 'DISABLE' if dis else '        ',
                             modes, iova))
                    if dis or not iova or not pool:
                        continue
                    off = iova - 0x100005000
                    blob = pool[off // 4: off // 4 + sz]
                    for reg, val in decode_blob(blob, names):
                        if isinstance(reg, str):
                            print('      %s %s' % (reg,
                                  ' '.join('%08x' % v for v in val if v is not None))
                                  if val is not None else '      ' + reg)
                        else:
                            print('      %s = 0x%08x' % (fmt_reg(reg, names),
                                                         val if val is not None else 0))
            i += 1 + cnt
        elif (dw >> 30) == 0b01:
            i += 1 + (dw & 0x7f)
        elif (dw >> 31) and not (dw >> 30) & 1:
            i += 2
        else:
            i += 1 + (((dw >> 16) & 0x3fff) + 1)

if __name__ == '__main__':
    main()
