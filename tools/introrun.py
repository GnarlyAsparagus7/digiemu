#!/usr/bin/env python3
"""Boot mk1 through its intro the way mk1 actually works, and hand over.

Digitakt II and Digitakt mk1 want opposite things during the intro, and
emu/pit.py only records the Digitakt II half:

  DT2   hold every channel. unblock force-satisfies the frame semaphore, and
        a real PIT3 tick posts it a second time, so the draw loop never exits.
  mk1   deliver PIT3, hold the rest. The intro PARKS on the frame semaphore
        (rungs 280M and 400M carry a waiter at frame_sem+4), and unblock
        cannot satisfy a wait that is already blocked, so the PIT3 tick is
        the only thing that can post it.

Measured on mk1 from boot400M with channels=(3,): the intro animates out
(342 -> 72 -> 0 lit) over 77 ticks and runs its exit sequence exactly once at
~25M, switching PIT3 off. 0x4006cb8e and 0x4006cb90 never execute; the
sequence starts at 0x4006cb92 and its 'pea frame_sem+8' at 0x4006cb94 is hit
once, which is the same instruction DT2's intro_done signature anchors on.

What was still missing is the other half of the handover: PIT0, PIT2 and
DMA timer 3 stay held through the intro and nothing releases them, so after
the intro exits no OS task runs and the display module never claims vector
208. This does both -- PIT3 during the intro, everything released at
intro_done -- and reports whether the OS actually comes up.
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
POST_ISR = 0x400E5CEC
INTRO_DONE = 0x4006CB94


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
    ap.add_argument('--budget', type=lambda s: int(s, 0), default=400_000_000)
    ap.add_argument('--chunk', type=lambda s: int(s, 0), default=10_000_000)
    ap.add_argument('--png', default='out/introrun.png')
    ap.add_argument('--save', default='',
                    help='write a snapshot here once the OS is up')
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)

    m, ev, st, pc, inq, at = longrun.build(
        args.snapshot, syx=args.syx, unblock=True, softfloat=True,
        bitmap=True, dsp=True)

    # PIT3 only while the intro owns vector 208; PIT0/PIT2 and DMA timer 3
    # stay held so they cannot disturb it (emu/pit.py: PIT2 during the intro
    # stops the OS tasks spawning at all).
    pit = Pits(m, channels=(3,), hold=False)
    dtim = Dtims(m, channels=(3,), hold=True)
    pits = Timers(pit, dtim)

    state = {'done': None, 'hits': 0}

    def handover(*_a):
        if state['done'] is not None:
            return
        state['done'] = True
        state['hits'] += 1
        # The intro has switched PIT3 off itself; widen to the OS set and let
        # the DMA timer through -- its ISR is the only thing at boot that
        # messages the queue the main application task blocks on.
        pit.channels = (3, 2, 0)
        pits.release()

    at(INTRO_DONE, handover)

    print('resume %s  vector=0x%08x PCSR=0x%04x'
          % (os.path.basename(args.snapshot), u32(m, PIT3_VECTOR_SLOT),
             u16(m, PIT3_BASE)))
    print('%6s %-8s %-8s %-12s %-6s %s'
          % ('instr', 'PCSR', 'lit', 'vector', 'intro', 'phase'))

    flipped = None
    total = 0
    while total < args.budget:
        pc, _e, _w = longrun.spin(m, pc, args.chunk, pits=pits)
        total += args.chunk
        slot = u32(m, PIT3_VECTOR_SLOT)
        buf = panel.read(m, prof.fb_front)
        lit = len(panel.lit(buf)) if buf else -1
        if flipped is None and slot == POST_ISR:
            flipped = total
            print('  >> display module claimed vector 208 at %dM' % (total // 1_000_000),
                  flush=True)
        phase = ('OS' if slot == POST_ISR else
                 ('post-intro' if state['done'] else 'intro'))
        print('%5dM 0x%04x   %-8d 0x%08x   %-6s %s'
              % (total // 1_000_000, u16(m, PIT3_BASE) or 0, lit, slot or 0,
                 'done' if state['done'] else 'live', phase), flush=True)

    print('')
    print('intro_done hook fired %d time(s)' % state['hits'])
    buf = panel.read(m, prof.fb_front)
    if buf:
        panel.write_png(buf, args.png, scale=6)
        print('screen (%d lit) -> %s' % (len(panel.lit(buf)), args.png))
    if flipped:
        print('VERDICT: the OS took over -- display module owns vector 208.')
    elif state['done']:
        print('VERDICT: the intro handed over but the OS did not claim '
              'vector 208.')
    else:
        print('VERDICT: the intro never reached its exit sequence.')


if __name__ == '__main__':
    main()
