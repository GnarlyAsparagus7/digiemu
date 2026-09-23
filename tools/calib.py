#!/usr/bin/env python3
"""Measure which physical control each wire code actually is.

Two separate mappings have been conflated so far:

  wire code   = channel*8 + bit, measured from the firmware's own queue_send
                record (tools/evtest.py) -- this part is solid
  code -> name  ASSUMED to be the 48-entry table indexed by that code

The second assumption is wrong: wire code 25 should be 'PLAY' under it, but
pressing it opens the RECORDER page. Rather than guess an offset, this
presses each code from a FRESH resume of the same snapshot and records what
the screen becomes. Distinct pages have distinct lit-pixel counts, so the
result identifies each control by its effect instead of by a label.

Fresh resume per code on purpose: presses are stateful, so pressing ten
things into one machine measures the tenth in the context of the other nine.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panel, panelin, symbols
from emu.dtmap import wire_for
from emu.uiresume import open_snapshot


def table_name(img, base, index):
    off = base - 0x40000400 + 4 * index
    ptr = struct.unpack_from('>I', img, off)[0]
    o = ptr - 0x40000400
    if not (0 <= o < len(img)):
        return '?'
    end = img.find(b'\x00', o)
    return img[o:end].decode('latin1') if 0 <= end - o < 24 else '?'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--codes', default='20-31')
    ap.add_argument('--settle', type=lambda s: int(s, 0), default=5_000_000)
    ap.add_argument('--probe', type=lambda s: int(s, 0), default=16_000_000)
    ap.add_argument('--out', default='out/calib')
    args = ap.parse_args()

    lo, hi = (int(x) for x in args.codes.split('-'))
    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    os.makedirs(args.out, exist_ok=True)

    # Label by EFFECT, not by table name: the 0x4018e254 table is the
    # hardware panel-test screen's label list, and the runtime UI dispatches
    # on a different internal enum -- so "GLOBAL" there opened the FILTER
    # page. The lit-pixel count identifies the page that actually appeared.
    print('%-5s %-6s %-14s %-9s %s'
          % ('code', 'ch/bit', 'test-label', 'lit', 'screen changed'))
    for code in range(lo, hi + 1):
        m, ev, st, pc, inq, at, pits = open_snapshot(
            args.snapshot, args.syx, prof, verbose=False)
        cap = panel.Capture(at, diff_addr=getattr(prof, 'panel_diff', None),
                            front_addr=prof.fb_front)
        pc, _e, _w = longrun.spin(m, pc, args.settle, pits=pits)
        base_lit = len(panel.lit(cap.frames[-1])) if cap.frames else -1

        wire = wire_for(code)
        if wire is None:
            print('%-5d (not on this panel)' % code)
            continue
        ch, bit = wire
        pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 1 << bit))
        pc, _e, _w = longrun.spin(m, pc, args.probe, pits=pits)
        pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 0))
        pc, _e, _w = longrun.spin(m, pc, args.probe, pits=pits)

        lit = len(panel.lit(cap.frames[-1])) if cap.frames else -1
        if cap.frames:
            panel.write_png(cap.frames[-1],
                            os.path.join(args.out, 'code%02d.png' % code),
                            scale=6)
        print('%-5d %d/%-4d %-14s %-9s %s'
              % (code, ch, bit, table_name(img, 0x4018e254, code), lit,
                 'YES' if lit != base_lit else 'no'), flush=True)


if __name__ == '__main__':
    main()
