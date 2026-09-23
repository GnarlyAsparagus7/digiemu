#!/usr/bin/env python3
"""Run on until the UI has SETTLED, then save. Fixes how gui.snap was made.

tools/introboot.py stops at the first untorn frame with more than --min-lit
pixels, which is the first frame of the main UI. That is the wrong moment.
On a blank +Drive the firmware is still doing its first-boot work at that
point -- it draws "INITIALIZING +DRIVE..." and then
"FACTORY PROJECT >> +DRIVE...", both from the display module (0x400e5398 and
0x400e55c2) -- and it interleaves those overlays with the main page. A
snapshot saved at the first main-UI frame therefore captures the machine
MID-FIRST-BOOT, and every resume of it replays the tail of that work: the
screen alternates between the main page and the +Drive overlay, which is
visible as flicker with the +Drive logo flashing up.

"Settled" is measured as: the last --quiet consecutive untorn frames are ALL
main-UI frames, i.e. none of them is one of the small progress pages. Frame
count rather than instruction count, because what matters is that the overlay
has stopped being drawn, not how long that took.

Untorn frames only -- panel.read at an arbitrary instant returns a frame torn
on a page boundary, so it cannot be used to decide this.
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panel, symbols
from emu.snapshot import save
from emu.uiresume import open_snapshot

GUI_FLAGS = dict(unblock=True, softfloat=True, bitmap=True, dsp=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', default='snapshots/Digitakt_OS1.53/gui.snap')
    ap.add_argument('--out', default='')
    ap.add_argument('--min-lit', type=int, default=1200,
                    help='a frame with more than this many lit pixels is the '
                         'main UI; anything less is a progress overlay')
    ap.add_argument('--quiet', type=int, default=120,
                    help='consecutive main-UI frames required to call it '
                         'settled')
    ap.add_argument('--budget', type=lambda s: int(s, 0), default=3_000_000_000)
    ap.add_argument('--chunk', type=lambda s: int(s, 0), default=50_000_000)
    ap.add_argument('--png', default='out/uisettle.png')
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)

    flags = dict(GUI_FLAGS)
    if prof.frame_sem is not None:
        flags['unblock_except'] = (prof.frame_sem,)
    m, ev, st, pc, inq, at, pits = open_snapshot(
        args.snapshot, args.syx, prof, **flags)

    hits = collections.Counter()
    for name in ('job_pump', 'mainloop'):
        addr = getattr(prof, name, None)
        if addr:
            at(addr, (lambda n: (lambda *_: hits.__setitem__(n, hits[n] + 1)))(name))

    cap = panel.Capture(at, diff_addr=getattr(prof, 'panel_diff', None),
                        front_addr=prof.fb_front)

    print('%6s %-8s %-9s %-10s %-9s %s'
          % ('instr', 'frames', 'overlays', 'quiet-run', 'jobs', 'state'))
    total = 0
    settled = None
    while total < args.budget:
        pc, _e, _w = longrun.spin(m, pc, args.chunk, pits=pits)
        total += args.chunk
        lits = [len(panel.lit(b)) for b in cap.frames]
        overlays = sum(1 for n in lits if n <= args.min_lit)
        # Length of the trailing run of main-UI frames.
        run = 0
        for n in reversed(lits):
            if n <= args.min_lit:
                break
            run += 1
        print('%5dM %-8d %-9d %-10d %-9d %s'
              % (total // 1_000_000, len(lits), overlays, run,
                 hits['job_pump'],
                 'settled' if run >= args.quiet else 'first-boot work'),
              flush=True)
        if run >= args.quiet:
            settled = total
            break

    print('')
    if settled is None:
        print('NOT SETTLED after %dM instructions -- the +Drive overlay is '
              'still being drawn.' % (args.budget // 1_000_000))
    else:
        print('SETTLED at %dM: %d consecutive main-UI frames with no overlay.'
              % (settled // 1_000_000, args.quiet))

    buf = cap.frames[-1] if cap.frames else None
    if buf:
        panel.write_png(buf, args.png, scale=6)
        print('screen (%d lit) -> %s' % (len(panel.lit(buf)), args.png))

    if args.out and settled is not None:
        os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
        ev['claim_checkpoint_component']('timers', pits)
        save(m, args.out, extra={'n': total, 'tasks': dict(ev.get('tasks', {}))},
             components=ev['checkpoint_components'],
             manifest=ev.get('checkpoint_manifest'))
        print('saved %s' % args.out)
    elif args.out:
        print('not saving: the UI never settled, so this snapshot would have '
              'the same fault as the one it replaces')


if __name__ == '__main__':
    main()
