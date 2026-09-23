#!/usr/bin/env python3
"""Find the real transport keys by their EFFECT ON STATE, not on pixels.

The panel-test label table is not the runtime key map: its "PLAY" (code 25)
is actually trig 2, which is why pressing it selected track 2 and why the
earlier conclusion "PLAY never reaches TransportView" was drawn while
pressing the wrong key entirely.

Screen diffing cannot find the transport keys either -- pressing PLAY on a
stopped, empty pattern barely changes a parameter page, which is why codes
0,1,2,6,9-15 all read as "no change". So this watches the things only a
transport key moves:

  0x4199dc2c  transport flag  (1 playing, 2 stopped)
  0x4006edf4  seq_transport_play entry
  0x40070218  seq_stop entry
  0x4003c9b0  TransportView::consumeKeyEvent

Each candidate is tried from a FRESH resume, because a press that starts the
transport changes what every later press means.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panelin, symbols
from emu.dtmap import wire_for
from emu.uiresume import open_snapshot

TRANSPORT_FLAG = 0x4199DC2C   # Digitakt mk1 OS 1.53 fallback only; rebound from
                              # prof.transport_state once the image is resolved
WATCH = [('transportView', 0x4003C9B0), ('seq_start', 0x4006EDF4),
         ('seq_stop', 0x40070218), ('flag_store', 0x4006F072)]


def u32(m, a):
    try:
        return struct.unpack('>I', m.uc.mem_read(a, 4))[0]
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--codes', default='0,1,2,6,9,10,11,12,13,14,15,16,17,18')
    ap.add_argument('--settle', type=lambda s: int(s, 0), default=4_000_000)
    ap.add_argument('--probe', type=lambda s: int(s, 0), default=10_000_000)
    args = ap.parse_args()

    codes = [int(c) for c in args.codes.split(',')]
    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    global TRANSPORT_FLAG
    TRANSPORT_FLAG = prof.transport_state or TRANSPORT_FLAG   # resolved per image

    print('%-5s %-7s %-9s %-9s %-9s %-10s %s'
          % ('code', 'ch/bit', 'flag', 'transpV', 'seqStart', 'flagStore', 'seqStop'))
    print('-' * 68)
    for code in codes:
        wire = wire_for(code)
        if wire is None:
            continue
        m, ev, st, pc, inq, at, pits = open_snapshot(
            args.snapshot, args.syx, prof, verbose=False)
        hits = {n: 0 for n, _ in WATCH}
        for name, addr in WATCH:
            at(addr, (lambda n: (lambda uc, a, s, d:
                                 hits.__setitem__(n, hits[n] + 1)))(name))
        pc, _e, _w = longrun.spin(m, pc, args.settle, pits=pits)

        ch, bit = wire
        pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 1 << bit))
        pc, _e, _w = longrun.spin(m, pc, args.probe, pits=pits)
        pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 0))
        pc, _e, _w = longrun.spin(m, pc, args.probe, pits=pits)

        flag = u32(m, TRANSPORT_FLAG)
        mark = '   <== TRANSPORT KEY' if (flag or hits['seq_start']) else ''
        print('%-5d %d/%-5d %-9s %-9d %-9d %-10d %d%s'
              % (code, ch, bit, flag, hits['transportView'], hits['seq_start'],
                 hits['flag_store'], hits['seq_stop'], mark), flush=True)


if __name__ == '__main__':
    main()
