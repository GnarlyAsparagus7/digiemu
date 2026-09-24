#!/usr/bin/env python3
"""Boot Digitakt mk1 from a ladder rung to a live UI, and optionally save it.

mk1 and Digitakt II want opposite things from the intro, and only the DT2
half is recorded in emu/pit.py:

  DT2   hold every PIT. unblock force-satisfies the frame semaphore and the
        animation runs unpaced; a real PIT3 tick would post it a second time
        and the draw loop would never reach its exit test.
  mk1   deliver PIT3 and leave the frame semaphore ALONE. Measured: with
        unblock satisfying it as well, the intro exits after 77 ticks instead
        of 180 frames and the machine is dead afterwards.

emu/longrun.py already offers the second policy -- "unblock_except lists
semaphore objects to leave alone, for waits you want to drive properly
instead" -- and it also installs the handoff that stops satisfying the frame
semaphore once the intro is over, "or the draw task busy-spins at prio 7 and
starves the rest of the system". Both are guarded on profile.intro_done and
profile.frame_sem, which are None on mk1, so on mk1 neither has ever run.
The measured mk1 values are below and belong in emu/symbols.py; they are
named here as well so this tool works before that edit lands.

    frame_sem      0x41988be4   pea operand of the intro's PIT3 handler
    intro_pit3_isr 0x4006c154   vector 208 through the whole intro
    intro_done     0x4006cb94   pea frame_sem+8, hit exactly once, opens the
                                exit sequence that switches PIT3 off

The run itself is emu/bootstrap.py's boot_to_ui(), which the portable app's
first run calls in-process; this is its command line, with the same flags,
defaults and printed lines. Two things changed with the move: the snapshot is
saved (to --out.tmp, then renamed) BEFORE the PNG is written, and an emulator
stop ends the run with its reason instead of spinning to the budget. The exit
code is 0 either way, as it always was.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu.bootstrap import StepFailed, boot_to_ui          # noqa: E402


def parser():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--budget', type=lambda s: int(s, 0), default=600_000_000)
    ap.add_argument('--chunk', type=lambda s: int(s, 0), default=20_000_000)
    ap.add_argument('--png', default='out/introboot.png')
    ap.add_argument('--out', default='', help='save a snapshot here once up')
    ap.add_argument('--min-lit', type=int, default=500)
    ap.add_argument('--no-except', action='store_true',
                    help='let unblock satisfy the frame semaphore (the DT2 '
                         'policy) -- for comparison')
    return ap


def _show(event):
    if event.text:
        print(event.text, flush=True)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        boot_to_ui(args.syx, args.snapshot, out=args.out or None,
                   progress=_show, min_lit=args.min_lit, budget=args.budget,
                   chunk=args.chunk, png=args.png or None,
                   intro_channels=(3,), except_frame_sem=not args.no_except)
    except StepFailed as exc:
        print('stopped: %s' % exc.reason, flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
