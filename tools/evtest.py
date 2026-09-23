#!/usr/bin/env python3
"""Is a panel press DECODED, not just delivered?

tools/intest.py already proves delivery: the ring pointer advances and the
driver's receive callback runs once per byte. That is not the same as the
firmware classifying those bytes as a button. The ground truth is the record
the firmware hands to `queue_send` -- see tools/panelsweep.py, which does this
against a cold snapshot with its own build flags.

This is the same idea against a mid-run UI snapshot, with no build flags to
match: hook queue_send, read its arguments, and diff the records seen while
idle against the records seen right after a press. Anything new is the press.

Record layout per panelsweep: 16 bytes, +0x00 type byte (0 button,
1 encoder), +0x07 code byte (channel*8 + bit + 1 for buttons).
"""
import argparse
import os
import struct
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panel, panelin, symbols
from emu.uiresume import open_snapshot

from unicorn.m68k_const import UC_M68K_REG_A7

QUEUE_SEND = 0x40001b7a


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--code', type=int, default=24)
    ap.add_argument('--settle', type=lambda s: int(s, 0), default=8_000_000)
    ap.add_argument('--probe', type=lambda s: int(s, 0), default=12_000_000)
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    m, ev, st, pc, inq, at, pits = open_snapshot(args.snapshot, args.syx, prof)

    seen = Counter()
    collecting = [False]

    def on_send(uc, addr, size, data):
        sp = uc.reg_read(UC_M68K_REG_A7)
        try:
            a0, a1 = struct.unpack('>II', uc.mem_read(sp + 4, 8))
        except Exception:
            return
        rec = b''
        if 0x40000000 <= a1 < 0x50000000:
            try:
                rec = bytes(uc.mem_read(a1, 16))
            except Exception:
                rec = b''
        key = (a0, rec.hex())
        seen[key] += 1
        if collecting[0] and rec:
            print('   queue=0x%08x  type=%d code=%d  raw=%s'
                  % (a0, rec[0], rec[7], rec.hex()), flush=True)

    at(QUEUE_SEND, on_send)

    print('== settling, to learn the background records ==', flush=True)
    pc, ex, why = longrun.spin(m, pc, args.settle, pits=pits)
    baseline = set(seen)
    print('   %d queue_send call(s), %d distinct record(s)'
          % (sum(seen.values()), len(baseline)), flush=True)
    for (q, r), n in seen.most_common(6):
        print('   background: queue=0x%08x x%-4d type=%s code=%s'
              % (q, n, r[0:2] or '?', r[14:16] or '?'))

    ch, bit = (args.code - 1) // 8, (args.code - 1) % 8
    print('\n== pressing code %d (channel %d bit %d) ==' % (args.code, ch, bit),
          flush=True)
    collecting[0] = True
    before = set(seen)
    pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 1 << bit))
    pc, ex, why = longrun.spin(m, pc, args.probe, pits=pits)
    pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 0))
    pc, ex, why = longrun.spin(m, pc, args.probe, pits=pits)
    collecting[0] = False

    new = [k for k in seen if k not in before]
    print('\n== result ==')
    print('new record shapes after the press: %d' % len(new))
    for q, r in new[:12]:
        if r:
            print('   queue=0x%08x  type=%d  CODE=%d  raw=%s'
                  % (q, int(r[0:2], 16), int(r[14:16], 16), r))
        else:
            print('   queue=0x%08x  (item not readable)' % q)
    if not new:
        print('   NONE -- the bytes arrive but the firmware does not classify')
        print('   them as a button on this build: the wire encoding differs.')


if __name__ == '__main__':
    main()
