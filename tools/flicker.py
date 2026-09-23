#!/usr/bin/env python3
"""Measure what the screen actually does over a run: flicker, and what flashes.

The GUI's status line reports one `panel` number per chunk, which is a sample,
not a census -- a screen that alternates between two pages at frame rate shows
up there as an occasional odd number and is easy to dismiss. This counts every
untorn frame instead and groups them, so "the UI is stable" and "the UI is
alternating with another page 40 times a second" cannot look alike.

Untorn frames only: panel.read at an arbitrary instant samples mid-flush and
returns a frame torn on a page boundary, whose lit count swings on its own
whether or not anything is animating. panel.Capture hooks the diff's entry,
which emu/panel.py documents as the one moment FRONT is a complete frame.

Also counts the job pump and the display frame post, because the suspected
cause couples them: if the display task's per-frame pend is being
force-satisfied it draws many frames per real one and starves the priority-2
job worker doing +Drive initialisation, so an over-drawing screen and a job
pump stuck at zero are one fault, not two.
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
    ap.add_argument('--budget', type=lambda s: int(s, 0), default=120_000_000)
    ap.add_argument('--chunk', type=lambda s: int(s, 0), default=20_000_000)
    ap.add_argument('--plain', action='store_true',
                    help='open with default build flags instead of the GUI set')
    ap.add_argument('--out', default='out/flicker')
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    print('display_sem = %s   job_pump = %s   display_frame_post = %s'
          % (hex(prof.display_sem) if prof.display_sem else None,
             hex(prof.job_pump) if getattr(prof, 'job_pump', None) else None,
             hex(getattr(prof, 'display_frame_post', None))
             if getattr(prof, 'display_frame_post', None) else None))

    flags = {} if args.plain else dict(GUI_FLAGS)
    if not args.plain and prof.frame_sem is not None:
        flags['unblock_except'] = (prof.frame_sem,)
    m, ev, st, pc, inq, at, pits = open_snapshot(
        args.snapshot, args.syx, prof, **flags)
    os.makedirs(args.out, exist_ok=True)

    hits = collections.Counter()
    for name in ('job_pump', 'mainloop', 'display_frame_post'):
        addr = getattr(prof, name, None)
        if addr:
            at(addr, (lambda n: (lambda *_: hits.__setitem__(n, hits[n] + 1)))(name))

    cap = panel.Capture(at, diff_addr=getattr(prof, 'panel_diff', None),
                        front_addr=prof.fb_front)

    total = 0
    seen_first = 0
    while total < args.budget:
        pc, _e, _w = longrun.spin(m, pc, args.chunk, pits=pits)
        total += args.chunk
        frames = cap.frames[seen_first:]
        seen_first = len(cap.frames)
        groups = collections.Counter(len(panel.lit(b)) for b in frames)
        # How often consecutive frames differ: a stable page is ~0, a page
        # alternating with another is ~1 per frame.
        flips = sum(1 for a, b in zip(frames, frames[1:]) if a != b)
        print('%5dM  frames=%-5d flips=%-5d  lit groups: %s   jobs=%d '
              'mainloop=%d'
              % (total // 1_000_000, len(frames), flips,
                 ', '.join('%d x%d' % (lit, n)
                           for lit, n in groups.most_common(4)),
                 hits['job_pump'], hits['mainloop']), flush=True)

    # One PNG per distinct page, named by its lit count, so whatever is
    # flashing can simply be looked at.
    by_lit = {}
    for buf in cap.frames:
        by_lit.setdefault(len(panel.lit(buf)), buf)
    print('')
    print('distinct pages seen: %s' % sorted(by_lit))
    for lit, buf in sorted(by_lit.items()):
        path = os.path.join(args.out, 'lit%05d.png' % lit)
        panel.write_png(buf, path, scale=6)
    print('wrote %d page image(s) to %s' % (len(by_lit), args.out))
    print('totals: job_pump=%d mainloop=%d display_frame_post=%d'
          % (hits['job_pump'], hits['mainloop'], hits['display_frame_post']))


if __name__ == '__main__':
    main()
