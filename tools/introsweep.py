#!/usr/bin/env python3
"""Which PIT channels does mk1's intro actually need? Measure, don't inherit.

emu/pit.py records a measurement made on Digitakt II: hold every channel
during the intro, because a real PIT3 tick posts the frame semaphore that
unblock is already satisfying and the draw loop then never exits. mk1 does
not behave that way. Held from any rung it does nothing at all for 600M
instructions -- blank screen, no draw loop, no handover -- because on mk1 the
intro PARKS on the frame semaphore (measured: rungs 280M and 400M carry a
waiter at frame_sem+4) and unblock cannot satisfy a wait that is already
blocked. The only thing that can post it is the very PIT3 tick upstream
holds off.

So this sweeps the channel sets against both a parked rung and an unparked
one and reports what each does, the same way the Digitakt II numbers in
emu/pit.py were obtained. Signals, cheapest first: PIT3 ticks (hits on the
intro's own handler), pixels lit, and the PIT3 vector slot, which stops
pointing at 0x4006c154 exactly when the intro hands over.
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
EXIT_SEQ = [0x4006CB8E, 0x4006CB90, 0x4006CB92, 0x4006CB94, 0x4006CBA4]

CONFIGS = [
    ('held', (), True),
    ('pit3', (3,), False),
    ('pit0+3', (3, 0), False),
    ('pit0', (0,), False),
    ('all', (3, 2, 0), False),
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


def trial(snapshot, syx, prof, name, channels, hold, budget, chunk):
    m, ev, st, pc, inq, at = longrun.build(
        snapshot, syx=syx, unblock=True, softfloat=True, bitmap=True, dsp=True)
    pits = Timers(Pits(m, channels=channels, hold=hold),
                  Dtims(m, channels=(3,), hold=hold))
    hits = {'isr': 0, 'exit': 0}
    at(INTRO_ISR, lambda *a: hits.__setitem__('isr', hits['isr'] + 1))
    for a_ in EXIT_SEQ:
        at(a_, lambda *a: hits.__setitem__('exit', hits['exit'] + 1))

    done = None
    total = 0
    while total < budget:
        pc, _e, _w = longrun.spin(m, pc, chunk, pits=pits)
        total += chunk
        if u32(m, PIT3_VECTOR_SLOT) != INTRO_ISR:
            done = total
            break
    buf = panel.read(m, prof.fb_front)
    lit = len(panel.lit(buf)) if buf else -1
    return {'name': name, 'isr': hits['isr'], 'exit': hits['exit'],
            'lit': lit, 'slot': u32(m, PIT3_VECTOR_SLOT),
            'pcsr': u16(m, PIT3_BASE), 'done': done, 'buf': buf}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--budget', type=lambda s: int(s, 0), default=80_000_000)
    ap.add_argument('--chunk', type=lambda s: int(s, 0), default=10_000_000)
    ap.add_argument('--only', default='')
    ap.add_argument('--out', default='out/introsweep')
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    os.makedirs(args.out, exist_ok=True)

    picks = [c for c in CONFIGS if not args.only or c[0] in args.only.split(',')]
    print('snapshot %s   budget %dM' % (os.path.basename(args.snapshot),
                                        args.budget // 1_000_000))
    print('%-9s %-10s %-9s %-8s %-12s %s'
          % ('config', 'PIT3 ticks', 'exitseq', 'lit', 'vector', 'handover'))
    for name, channels, hold in picks:
        try:
            r = trial(args.snapshot, args.syx, prof, name, channels, hold,
                      args.budget, args.chunk)
        except Exception as exc:
            print('%-9s FAILED: %s' % (name, exc), flush=True)
            continue
        print('%-9s %-10d %-9d %-8d 0x%08x   %s'
              % (r['name'], r['isr'], r['exit'], r['lit'], r['slot'] or 0,
                 ('YES at %dM' % (r['done'] // 1_000_000)) if r['done']
                 else 'no'), flush=True)
        if r['buf']:
            panel.write_png(r['buf'],
                            os.path.join(args.out, '%s.png' % name), scale=6)


if __name__ == '__main__':
    main()
