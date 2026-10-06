#!/usr/bin/env python3
"""rdparse.py — парсер RD-дампов turnip (FD_RD_DUMP=full -> /tmp/tu_*.rd, gzip).

Записи: (u32 type, u32 size, body[size]):
  t=3 : BO      (u32 lo, u32 size, u32 hi) -> iova
  t=6 : IB addr (u32 lo, u32 ndw,   u32 hi)
  t=12: содержимое последнего BO (тело = байты)

Примеры:
  rdparse.py file.rd                       # сводка
  rdparse.py file.rd --extract 0xIOVA out.txt  # содержимое BO как текст dword'ов
  rdparse.py file.rd --scan 0x1234abcd     # найти BO с таким dword'ом
"""
import gzip, struct, sys

def parse(path):
    with open(path, 'rb') as f:
        magic = f.read(2)
    fh = gzip.open(path, 'rb') if magic == b'\x1f\x8b' else open(path, 'rb')
    data = fh.read()
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
            iova = bos[-1][0] if bos else 0
            contents.append((iova, body))
    return bos, addrs, contents

def as_dws(body):
    n = len(body) // 4
    return struct.unpack('<%dI' % n, body[:n * 4])

def main():
    args = sys.argv[1:]
    mode = 'summary'
    if '--extract' in args:
        mode = 'extract'
        i = args.index('--extract')
        want, outpath = int(args[i + 1], 0), args[i + 2]
        args = args[:i] + args[i + 3:]
    elif '--scan' in args:
        mode = 'scan'
        i = args.index('--scan')
        needle = int(args[i + 1], 0)
        args = args[:i] + args[i + 3:]
    path = args[0]
    bos, addrs, contents = parse(path)

    if mode == 'summary':
        print('== %s: BOs=%d contents=%d' % (path, len(bos), len(contents)))
        for iova, size in bos:
            print('   BO  0x%011x size=0x%x' % (iova, size))
        for a, ndw in addrs:
            print('   IB  0x%011x ndw=%d' % (a, ndw))
        for iova, body in contents:
            dws = as_dws(body)
            nz = sum(1 for d in dws if d)
            print('   cnt 0x%011x dws=%d nonzero=%d' % (iova, len(dws), nz))
    elif mode == 'scan':
        for iova, body in contents:
            dws = as_dws(body)
            hits = [j for j, d in enumerate(dws) if d == needle]
            if hits:
                print('0x%x: %d dws, needle at dw %s'
                      % (iova, len(dws), ','.join(map(str, hits[:8]))))
    elif mode == 'extract':
        for iova, body in contents:
            if iova == want:
                dws = as_dws(body)
                with open(outpath, 'w') as f:
                    for j in range(0, len(dws), 8):
                        f.write('%04x: %s\n'
                                % (j * 4, ' '.join('%08x' % d
                                                   for d in dws[j:j+8])))
                print('extracted 0x%x -> %s (%d dws)'
                      % (iova, outpath, len(dws)))
                return
        print('iova 0x%x not found' % want, file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
