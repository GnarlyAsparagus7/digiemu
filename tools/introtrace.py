#!/usr/bin/env python3
"""Trace mk1's intro exit: who switches PIT3 off, and why nobody takes over.

Measured so far, from boot400M with PIT3 delivered and everything else held:
the intro unparks and ticks 77 times, then stops ticking -- and the count is
identical whether the budget is 60M or 450M, so it is not slow, it has
stopped. The exit-sequence addresses are hit, and one of them
(0x4006cba4) is 'move.w d1,(0xfc08c000)', the write that switches PIT3 off.
Yet the PIT3 vector slot never stops pointing at the intro's handler, so the
display module never claims vector 208 the way it has in ui.snap.

This prints, per chunk, the PIT3 control register alongside the hit counts
for each site that touches PIT3, so "the intro exited and switched its timer
off" can be told apart from "something disabled PIT3 out from under a
still-running intro". The addresses are grouped by what the byte scan showed
each one does rather than lumped into a single counter, because a single
counter cannot answer that question.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panel, symbols
from emu.dtim import Dtims, Timers
from emu.pit import Pits

PIT3_VECTOR_SLOT = 0x40000340
PIT3_BASE = 0xFC08C000
INTRO_ISR = 0x4006C154

SITES = [
    (0x4006C154, 'intro PIT3 ISR'),
    (0x4006C280, 'PIT3 reconfigure (write PCSR,PMR)'),
    (0x4006C296, 'PIT3 enable (or.l d1,d0 -> PCSR)'),
    (0x4006C29E, 'pea frame_sem+8 (post/park helper)'),
    (0x4006CB8E, 'exit-1'),
    (0x4006CB90, 'exit-2'),
    (0x4006CB92, 'exit-3 (addq #4,a7)'),
    (0x4006CB94, 'exit-4 (pea frame_sem+8)'),
    (0x4006CB9A, 'exit-5 (clr.w d1)'),
    (0x4006CBA4, 'exit-6 (move.w d1,PCSR = PIT3 OFF)'),
    (0x4006CBB0, 'exit-7 (jsr 0x40001840)'),
    (0x400E5CEC, 'display module PIT3 ISR'),
    (0x400E5DFA, 'display module exit seq'),
]


def u32(m, a):
    try:
        return struct.unpack('>I', m.uc.mem_read(a, 4))[0]
    except Exception:
        return None


def u16(m, a):
    try:
        return struct.unpack('>H', m.uc.mem_read(a, 2))[0]
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--channels', default='3',
                    help='comma-separated PIT channels to deliver, or empty')
    ap.add_argument('--dtim-hold', action='store_true')
    ap.add_argument('--budget', type=lambda s: int(s, 0), default=200_000_000)
    ap.add_argument('--chunk', type=lambda s: int(s, 0), default=10_000_000)
    ap.add_argument('--png', default='out/introtrace.png')
    args = ap.parse_args()

    channels = tuple(int(c) for c in args.channels.split(',') if c.strip())
    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)

    m, ev, st, pc, inq, at = longrun.build(
        args.snapshot, syx=args.syx, unblock=True, softfloat=True,
        bitmap=True, dsp=True)
    pits = Timers(Pits(m, channels=channels, hold=False),
                  Dtims(m, channels=(3,), hold=args.dtim_hold))

    hits = {}
    for addr, _label in SITES:
        def mk(a_):
            return lambda *_: hits.__setitem__(a_, hits.get(a_, 0) + 1)
        at(addr, mk(addr))

    print('snapshot=%s channels=%s dtim_hold=%s'
          % (os.path.basename(args.snapshot), channels or '(none)',
             args.dtim_hold))
    print('%6s %-8s %-8s %-12s %s' % ('instr', 'PCSR', 'lit', 'vector', 'ISR'))
    total = 0
    while total < args.budget:
        pc, _e, _w = longrun.spin(m, pc, args.chunk, pits=pits)
        total += args.chunk
        buf = panel.read(m, prof.fb_front)
        print('%5dM 0x%04x   %-8d 0x%08x   %d'
              % (total // 1_000_000, u16(m, PIT3_BASE) or 0,
                 len(panel.lit(buf)) if buf else -1,
                 u32(m, PIT3_VECTOR_SLOT) or 0, hits.get(INTRO_ISR, 0)),
              flush=True)

    print('')
    print('== site hits ==')
    for addr, label in SITES:
        print('  0x%08x  %-6d  %s' % (addr, hits.get(addr, 0), label))

    buf = panel.read(m, prof.fb_front)
    if buf:
        panel.write_png(buf, args.png, scale=6)
        print('screen (%d lit) -> %s' % (len(panel.lit(buf)), args.png))


if __name__ == '__main__':
    main()
