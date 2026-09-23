#!/usr/bin/env python3
"""Does a panel press actually reach the firmware?

Driving the panel and watching the screen cannot tell "the press never
arrived" apart from "the press arrived and this screen looks the same", and
those need completely different fixes. So this instruments the delivery path
itself:

  * the UART8 RX vector (154) being raised,
  * the driver's own receive callback running -- its address is a POINTER at
    profile.uart8_rx_callback, so the pointer is read and the code it points
    at is hooked,
  * the ring write pointer (TCD34 DADDR) advancing,
  * and, for contrast, whether the framebuffer changed at all.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panel, panelin, symbols
from emu.uiresume import open_snapshot


def u32(m, addr):
    try:
        return struct.unpack('>I', m.uc.mem_read(addr, 4))[0]
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--code', type=int, default=24, help='button code to press')
    ap.add_argument('--step', type=lambda s: int(s, 0), default=20_000_000)
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    m, ev, st, pc, inq, at, pits = open_snapshot(
        args.snapshot, args.syx, prof)

    print('uart8_ring_ptr  = 0x%08x -> ring base 0x%08x'
          % (prof.uart8_ring_ptr, u32(m, prof.uart8_ring_ptr) or 0))
    print('uart8_rx_callback slot 0x%08x -> fn 0x%08x'
          % (prof.uart8_rx_callback, u32(m, prof.uart8_rx_callback) or 0))
    print('TCD34 DADDR     = 0x%08x' % (u32(m, panelin.TCD34_DADDR) or 0))
    vec = u32(m, 4 * panelin.RX_VECTOR)
    print('vector %d slot   -> 0x%08x' % (panelin.RX_VECTOR, vec or 0))

    hits = {'rx_cb': 0, 'vec': 0}
    cb = u32(m, prof.uart8_rx_callback)
    if cb and 0x40000000 <= cb < 0x40400000:
        at(cb, lambda uc, a, s, d: hits.__setitem__('rx_cb', hits['rx_cb'] + 1))
    if vec and 0x40000000 <= vec < 0x40400000:
        at(vec, lambda uc, a, s, d: hits.__setitem__('vec', hits['vec'] + 1))

    fb = getattr(prof, 'fb_front', None)
    before = panel.read(m, fb) if fb else None

    daddr0 = u32(m, panelin.TCD34_DADDR)
    ch, bit = (args.code - 1) // 8, (args.code - 1) % 8
    print('\npressing code %d -> channel %d bit %d' % (args.code, ch, bit))
    pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 1 << bit))
    pc, _e, _w = longrun.spin(m, pc, args.step, pits=pits)
    pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 0))
    pc, _e, _w = longrun.spin(m, pc, args.step, pits=pits)
    daddr1 = u32(m, panelin.TCD34_DADDR)

    after = panel.read(m, fb) if fb else None
    print('\nDADDR  %08x -> %08x  (advanced %d)'
          % (daddr0 or 0, daddr1 or 0, (daddr1 or 0) - (daddr0 or 0)))
    print('rx vector handler ran : %d' % hits['vec'])
    print('rx callback ran       : %d' % hits['rx_cb'])
    if before is not None and after is not None:
        print('framebuffer changed   : %s  (%d -> %d lit)'
              % (before != after, len(panel.lit(before)), len(panel.lit(after))))


if __name__ == '__main__':
    main()
