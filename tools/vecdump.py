#!/usr/bin/env python3
"""Dump the guest's exception vector table and locate the sequencer tick.

0x4006e75a is the lea inside the handler, not its entry -- one finding put the
enclosing handler at 0x4006e756. Rather than guess which address the vector
holds, read the table and look for any slot pointing into that function.

The table's base is VBR, not necessarily 0, so VBR is read first.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, symbols
from emu.uiresume import open_snapshot


def u32(m, a):
    try:
        return struct.unpack('>I', m.uc.mem_read(a, 4))[0]
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--near', default='0x4006e756',
                    help='report vectors pointing within 0x200 of this')
    args = ap.parse_args()

    near = int(args.near, 16)
    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    m, ev, st, pc, inq, at, pits = open_snapshot(args.snapshot, args.syx, prof)
    pc, _e, _w = longrun.spin(m, pc, 4_000_000, pits=pits)

    vbr = None
    try:
        vbr = m.ctlregs.get(0x801) if hasattr(m, 'ctlregs') else None
    except Exception:
        vbr = None
    print('VBR (from machine ctlregs) = %s' % (('0x%08x' % vbr) if vbr else vbr))

    for base in ([vbr] if vbr else []) + [0x00000000, 0x40000000]:
        if base is None:
            continue
        hits = []
        for n in range(256):
            v = u32(m, base + 4 * n)
            if v and abs(v - near) <= 0x200:
                hits.append((n, v))
        print('')
        print('table base 0x%08x: %d slot(s) within 0x200 of 0x%08x'
              % (base, len(hits), near))
        for n, v in hits:
            print('   vector %-4d (INTC source %-3d) -> 0x%08x' % (n, n - 64, v))
        if not hits:
            populated = sum(1 for n in range(256)
                            if (u32(m, base + 4 * n) or 0) >> 24 == 0x40)
            print('   (%d/256 slots hold a 0x40xxxxxx pointer)' % populated)

    # Also show the whole INTC-source region of the most plausible table.
    base = 0x40000000
    print('')
    print('INTC sources 32..63 (vectors 96..127) at base 0x%08x:' % base)
    for n in range(96, 128):
        v = u32(m, base + 4 * n)
        if v:
            print('   vector %-4d source %-3d -> 0x%08x' % (n, n - 64, v))


if __name__ == '__main__':
    main()
