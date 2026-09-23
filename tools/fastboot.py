#!/usr/bin/env python3
"""Boot with the soft-float HLE on, and watch for the milestones by address.

emu/softfloat.py exists because on Digitakt II 93% of emulated instructions
were the intro's float-heavy particle simulation. longrun.build defaults it
OFF, so every run so far has paid full emulated cost for mk1's equivalent --
the 0x40123xxx/0x40124xxx cluster the profiler kept showing, which includes
__floatsidf. This turns it on and reports, by direct code hook, whether the
boot reaches the points that matter:

  dtim3_init   0x4005f17e   programs DTMR3 and installs vector 99's ISR --
                            the tick that wakes the whole user interface
  dtim3_isr    0x4005f150   that ISR actually running
  mainloop     profile.mainloop
  job_pump     profile.job_pump
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panel, symbols
from emu.dtim import Dtims, Timers
from emu.pit import Pits, intro_running

DTIM3_INIT = 0x4005f17e
DTIM3_ISR = 0x4005f150


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--step', type=lambda s: int(s, 0), default=50_000_000)
    ap.add_argument('--shots', type=int, default=12)
    ap.add_argument('--no-softfloat', action='store_true')
    ap.add_argument('--png', default='out/fast')
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)

    m, ev, st, pc, inq, at = longrun.build(
        args.snapshot, syx=args.syx, softfloat=not args.no_softfloat)

    seen = {}
    marks = [('dtim3_init', DTIM3_INIT), ('dtim3_isr', DTIM3_ISR)]
    for name in ('mainloop', 'job_pump', 'display_start'):
        addr = getattr(prof, name, None)
        if addr:
            marks.append((name, addr))

    def watch(name):
        def hook(uc, a, s_, d):
            seen[name] = seen.get(name, 0) + 1
        return hook

    for name, addr in marks:
        at(addr, watch(name))
    print('watching: %s' % ', '.join('%s@0x%08x' % (n, a) for n, a in marks),
          flush=True)

    # Without a Timers, longrun.spin services no interrupt source at all:
    # the firmware runs with a stopped clock and every tick-driven thing
    # looks deadlocked. emu/gui.py builds exactly this pair.
    intro = intro_running(m, getattr(prof, 'intro_pit3_isr', None))
    pits = Timers(Pits(m, hold=intro), Dtims(m, channels=(1, 3), hold=intro))
    print('timers armed (held=%s)' % pits.held, flush=True)

    os.makedirs(args.png, exist_ok=True)
    fb_front = getattr(prof, 'fb_front', None)
    total = 0
    for shot in range(args.shots):
        pc, executed, why = longrun.spin(m, pc, args.step, pits=pits)
        total += executed
        buf = panel.read(m, fb_front) if fb_front else None
        lit = len(panel.lit(buf)) if buf else -1
        print('[%2d] %5dM instrs  lit=%-5d  %s'
              % (shot, total // 1_000_000, lit,
                 ' '.join('%s=%d' % (n, seen.get(n, 0)) for n, _ in marks)),
              flush=True)
        if buf:
            panel.write_png(buf, os.path.join(args.png, '%02d.png' % shot),
                            scale=6)
        if seen.get('mainloop'):
            print('\nMAIN LOOP REACHED', flush=True)
            break

    print('\nfinal: %s' % seen)


if __name__ == '__main__':
    main()
