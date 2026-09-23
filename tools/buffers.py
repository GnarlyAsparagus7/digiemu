#!/usr/bin/env python3
"""Is the flicker two pages being DRAWN, or one buffer being read wrongly?

The panel is double-buffered and emu/gui.py warns that once the diff has
swapped, FRONT is the buffer being rendered INTO rather than the one on the
panel. So "the screen alternates between the main page and the +Drive
overlay" has two very different explanations:

  drawing   the firmware really is drawing both, and the panel really does
            flicker on hardware too
  sampling  each page lives in its own buffer and stays there, and what
            alternates is only which buffer the harness happens to read

They are told apart by reading BOTH buffers at the same instant. If FRONT and
BACK each hold a stable, different page, it is sampling. If both buffers show
the same page and that page changes over time, the firmware is redrawing.
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panel, symbols
from emu.uiresume import open_snapshot

GUI_FLAGS = dict(unblock=True, softfloat=True, bitmap=True, dsp=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', default='snapshots/Digitakt_OS1.53/gui.snap')
    ap.add_argument('--samples', type=int, default=24)
    ap.add_argument('--step', type=lambda s: int(s, 0), default=2_000_000)
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    front = prof.fb_front
    back = getattr(prof, 'fb_back', None)
    print('fb_front = %s   fb_back = %s'
          % (hex(front) if front else None, hex(back) if back else None))
    if back is None:
        raise SystemExit('fb_back did not resolve; cannot compare buffers')

    flags = dict(GUI_FLAGS)
    if prof.frame_sem is not None:
        flags['unblock_except'] = (prof.frame_sem,)
    m, ev, st, pc, inq, at, pits = open_snapshot(
        args.snapshot, args.syx, prof, **flags)

    print('%4s %-10s %-10s %s' % ('#', 'FRONT lit', 'BACK lit', 'same?'))
    seen = collections.Counter()
    for i in range(args.samples):
        pc, _e, _w = longrun.spin(m, pc, args.step, pits=pits)
        f = panel.read(m, front)
        b = panel.read(m, back)
        fl = len(panel.lit(f)) if f else -1
        bl = len(panel.lit(b)) if b else -1
        seen[(fl > 1200, bl > 1200)] += 1
        print('%4d %-10d %-10d %s'
              % (i, fl, bl, 'yes' if f == b else 'no'), flush=True)

    print('')
    print('front-is-mainUI / back-is-mainUI combinations seen:')
    for (fm, bm), n in seen.most_common():
        print('  front=%-8s back=%-8s  x%d'
              % ('mainUI' if fm else 'overlay', 'mainUI' if bm else 'overlay',
                 n))


if __name__ == '__main__':
    main()
