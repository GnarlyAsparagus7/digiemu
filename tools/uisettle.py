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

The run itself is emu/bootstrap.py's settle_ui(), which the portable app's
first run calls in-process; this is its command line, with the same flags,
defaults and printed lines. What changed with the move: frames are counted as
they are drawn instead of through panel.Capture, whose 4096-frame cap froze
the quiet-run count before a first boot on a fresh card (~4100 frames) could
finish; a task created during the run no longer crashes the save; the
snapshot is saved (to --out.tmp, then renamed) BEFORE the PNG; and an
emulator stop ends the run with its reason. The exit code is 0 either way.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu.bootstrap import StepFailed, settle_ui           # noqa: E402


def parser():
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
    return ap


def _show(event):
    if event.text:
        print(event.text, flush=True)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        settle_ui(args.syx, args.snapshot, out=args.out or None,
                  progress=_show, min_lit=args.min_lit, quiet=args.quiet,
                  budget=args.budget, chunk=args.chunk,
                  png=args.png or None, except_frame_sem=True)
    except StepFailed as exc:
        print('stopped: %s' % exc.reason, flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
