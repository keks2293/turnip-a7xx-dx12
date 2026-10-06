#!/usr/bin/env python3
"""pm4dec3.py dump.txt — декодер PM4-стрима turnip из текста "off: dw dw ...".

Форматы:
  T7: bit31-29=011, parity(op) bit23, op=(dw>>16)&0x7f, parity(cnt) bit15,
      cnt=dw&0x3fff
  T4: bit30=1, reg=(dw>>8)&0x3ffff, cnt=dw&0x7f  (bit27 = флаг, печатаем)
  T0: reg=dw&0x7fff, (cnt-1)=(dw>>16)&0x3fff -> payload cnt dw
  T2: bit31=1,bit30=0 -> IB: cnt=dw&0x3f, адрес в следующем dw
"""
import sys

T7 = {
    0x3d: 'MEM_WRITE', 0x47: 'COND_REG_EXEC', 0x46: 'EVENT_WRITE',
    0x43: 'SET_DRAW_STATE', 0x5c: 'CONTEXT_REG_BUNCH',
    0x38: 'DRAW_INDX_OFFSET', 0x28: 'DRAW_INDIRECT', 0x24: 'DRAW_AUTO',
    0x45: 'SET_MARKER', 0x64: 'SET_VISIBILITY_OVERRIDE', 0x23: 'VIZ_QUERY',
    0x22: 'WAIT_REG_MEM', 0x60: 'REG_TEST', 0x03: 'IB_PFD', 0x04: 'IB_PFP',
    0x4c: 'MEM_TO_REG', 0x49: 'REG_WRITE', 0x21: 'CP_INCREMENT', 0x4b: 'SET_PSEUDO_REG',
    0x26: 'EVENT_WRITE7', 0x53: 'WAIT_FOR_IDLE', 0x48: 'COND_WRITE',
    0x4f: 'STRM_TO_REG', 0x5e: 'CONTEXT_REG_BUNCH2',
}

DRAW_GROUP = ['PROGRAM_CONFIG', 'VS', 'VS_BINNING', 'HS', 'DS', 'GS',
              'GS_BINNING', 'VPC', 'FS', 'VB', 'CONST', 'DESC_SETS',
              'DESC_SETS_LOAD', 'VS_PARAMS', 'FS_PARAMS',
              'INPUT_ATTACHMENTS_GMEM', 'INPUT_ATTACHMENTS_SYSMEM',
              'LRZ_AND_DEPTH_PLANE', 'PRIM_MODE_GMEM', 'DYNAMIC+0']


def gname(g):
    return DRAW_GROUP[g] if g < len(DRAW_GROUP) else 'G%d' % g


def main(path):
    dws = []
    for line in open(path):
        if ':' not in line:
            continue
        _, rest = line.split(':', 1)
        for x in rest.split():
            dws.append(int(x, 16))
    print('# %s: %d dwords' % (path, len(dws)))
    i = 0
    while i < len(dws):
        dw = dws[i]
        if (dw >> 29) == 0b011:                      # T7
            op, cnt = (dw >> 16) & 0x7f, dw & 0x3fff
            name = T7.get(op, 'op=0x%02x' % op)
            tail = ''
            p = dws[i + 1:i + 1 + cnt]
            if op == 0x3d and cnt >= 3:              # MEM_WRITE
                tail = 'addr=0x%x val=0x%08x' % (p[0] | (p[1] << 32), p[2])
            elif op == 0x47 and cnt >= 2:            # COND_REG_EXEC
                tail = 'mode=0x%08x dwords=%d' % (p[0], p[1])
            elif op == 0x5c:                         # CONTEXT_REG_BUNCH
                first = ' '.join('%08x' % d for d in p[:6])
                tail = 'pairs=%d first: %s' % (cnt // 2, first)
            elif op == 0x43:                         # SET_DRAW_STATE
                ents = []
                for j in range(0, cnt, 3):
                    ctrl = p[j]
                    ents.append('G%d cnt=%d d%s BIN%d GMEM%d SYS%d'
                                % ((ctrl >> 24) & 0x3f, ctrl & 0xffff,
                                   'is' if (ctrl >> 17) & 1 else ' ',
                                   (ctrl >> 20) & 1, (ctrl >> 21) & 1,
                                   (ctrl >> 22) & 1))
                tail = ' | '.join(ents)
            elif op == 0x38:                         # DRAW_INDX_OFFSET
                if cnt >= 3:
                    ini = p[0]
                    tail = ('** DRAW ** init=0x%08x (prim=%d src=%d vis=%d '
                            'isize=%d) inst=%d vtx=%d'
                            % (ini, ini & 0x3f, (ini >> 6) & 3,
                               (ini >> 8) & 3, (ini >> 10) & 3,
                               p[1] if len(p) > 1 else -1,
                               p[2] if len(p) > 2 else -1))
            print('%6d: T7 %-18s cnt=%4d %s' % (i, name, cnt, tail))
            i += 1 + cnt
        elif (dw >> 30) == 0b01:                     # T4
            reg, cnt = (dw >> 8) & 0x3ffff, dw & 0x7f
            flag = ' F27' if dw & 0x08000000 else ''
            vals = ' '.join('%08x' % d for d in dws[i + 1:i + 1 + cnt])
            print('%6d: T4 reg=0x%05x cnt=%4d%s %s' % (i, reg, cnt, flag, vals))
            i += 1 + cnt
        elif (dw >> 31) == 1 and ((dw >> 30) & 1) == 0:  # T2/IB
            cnt = dw & 0x3f
            addr = dws[i + 1] if i + 1 < len(dws) else 0
            print('%6d: T2 IB          cnt=%4d addr=0x%08x' % (i, cnt, addr))
            i += 2
        else:                                          # T0
            reg, cnt = dw & 0x7fff, ((dw >> 16) & 0x3fff) + 1
            vals = ' '.join('%08x' % d for d in dws[i + 1:i + 1 + cnt])
            print('%6d: T0 reg=0x%04x cnt= %4d %s' % (i, reg, cnt, vals))
            i += 1 + cnt


if __name__ == '__main__':
    main(sys.argv[1])
