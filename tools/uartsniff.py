#!/usr/bin/env python3
"""Show what the ColdFire is sending to the panel MCU over UART8.

emu/edma.py's channel-35 model lands transmitted bytes in ev['uart_out'].
Printed as text they look like noise, because this is not a console: UART8
is the panel link, and the traffic is a framed protocol. Dumped as hex with
its repeat structure exposed, a periodic query -- something the firmware
sends over and over and is presumably waiting to be answered -- is obvious.
"""
import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import longrun


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--instrs', type=lambda s: int(s, 0), default=40_000_000)
    args = ap.parse_args()

    m, ev, st, pc, inq, at = longrun.build(args.snapshot, syx=args.syx)
    pc, executed, why = longrun.spin(m, pc, args.instrs)

    out = bytes(bytearray(ev.get('uart_out', b'')))
    print('ran %d instrs, stop=%s' % (executed, why))
    print('UART8 TX bytes captured: %d' % len(out))
    if not out:
        print('nothing transmitted')
        return

    print('\nfirst 96 bytes:')
    for i in range(0, min(96, len(out)), 16):
        chunk = out[i:i + 16]
        print('  %04x  %-48s' % (i, ' '.join('%02x' % b for b in chunk)))

    print('\nbyte histogram (top 12):')
    for b, n in Counter(out).most_common(12):
        print('  %02x  %6d  %5.1f%%' % (b, n, 100.0 * n / len(out)))

    # Look for a repeating period: the panel link is framed, so a stuck
    # firmware re-sends the same frame forever.
    print('\nrepeat period search:')
    for period in range(2, 65):
        if len(out) < period * 8:
            continue
        ref = out[:period]
        reps = sum(1 for k in range(1, min(40, len(out) // period))
                   if out[k * period:(k + 1) * period] == ref)
        total = min(40, len(out) // period) - 1
        if total > 0 and reps / total > 0.8:
            print('  period %d: %d/%d identical frames -> %s'
                  % (period, reps, total, ref.hex(' ')))
            break
    else:
        print('  no single dominant period in the first frames')


if __name__ == '__main__':
    main()
