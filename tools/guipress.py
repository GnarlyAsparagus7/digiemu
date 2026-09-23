#!/usr/bin/env python3
"""Press panel keys against the GUI's own snapshot and capture the result.

tools/dtdrive.py does the same thing for ui.snap, which is built with default
build flags. gui.snap is built with the GUI's flags and with
unblock_except=(frame_sem,), and unblock_except is part of the checkpoint
build manifest -- so a snapshot saved under one policy will not open under the
other and the two tools cannot be merged by dropping a flag.

This is the one that matches what the window actually runs, so a key that
works here works when a person clicks it.

Codes are the firmware's own 0-based control numbers, and the wire position
for each is MEASURED (emu/dtmap.py), not computed: this panel's matrix is
scanned in an order that panelin.code_for()'s channel*8+bit+1 gets wrong.
Useful ones: 10 PLAY, 11 STOP, 4 BANK, 7 SAMPLING, 8 TEMPO, 19-23 the
TRIG/SRC/FLTR/AMP/LFO page buttons, 24-39 trigs 1-16.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panel, panelin, symbols
from emu.dtmap import wire_for
from emu.uiresume import open_snapshot

GUI_FLAGS = dict(unblock=True, softfloat=True, bitmap=True, dsp=True)


def tap(m, prof, pits, pc, code, step):
    """Press and release one control through the ordinary panel path."""
    wire = wire_for(code)
    if wire is None:
        raise SystemExit('code %d is not on this panel' % code)
    ch, bit = wire
    pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 1 << bit))
    pc, _e, _w = longrun.spin(m, pc, step, pits=pits)
    pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 0))
    pc, _e, _w = longrun.spin(m, pc, step, pits=pits)
    return pc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', default='snapshots/Digitakt_OS1.53/gui.snap')
    ap.add_argument('--codes', default='21',
                    help='comma-separated control codes to press in turn')
    ap.add_argument('--settle', type=lambda s: int(s, 0), default=8_000_000)
    ap.add_argument('--step', type=lambda s: int(s, 0), default=12_000_000)
    ap.add_argument('--out', default='out/guipress')
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    if prof.frame_sem is None:
        raise SystemExit('frame_sem did not resolve; see emu/symbols.py')

    m, ev, st, pc, inq, at, pits = open_snapshot(
        args.snapshot, args.syx, prof,
        unblock_except=(prof.frame_sem,), **GUI_FLAGS)
    os.makedirs(args.out, exist_ok=True)

    # Untorn frames only: panel.read at an arbitrary instant samples mid-flush
    # and returns a frame torn on a page boundary. Capture hooks the diff's
    # entry, which emu/panel.py documents as the one moment FRONT is complete.
    cap = panel.Capture(at, diff_addr=getattr(prof, 'panel_diff', None),
                        front_addr=prof.fb_front)

    pc, _e, _w = longrun.spin(m, pc, args.settle, pits=pits)
    base = cap.frames[-1] if cap.frames else None
    base_lit = len(panel.lit(base)) if base else -1
    print('resting screen: %d lit' % base_lit)
    if base:
        panel.write_png(base, os.path.join(args.out, 'rest.png'), scale=6)

    names = panelin.control_names(m, prof, 'button') or {}
    prev = base
    for code in (int(c) for c in args.codes.split(',')):
        ch, bit = wire_for(code)
        pc = tap(m, prof, pits, pc, code, args.step)
        now = cap.frames[-1] if cap.frames else None
        lit = len(panel.lit(now)) if now else -1
        changed = (now is not None and prev is not None and now != prev)
        print('  code %-3d (ch %d bit %d) %-14s lit=%-6d screen %s'
              % (code, ch, bit, names.get(code, '?'), lit,
                 'CHANGED' if changed else 'unchanged'), flush=True)
        if now:
            panel.write_png(now, os.path.join(args.out, 'code%02d.png' % code),
                            scale=6)
        prev = now


if __name__ == '__main__':
    main()
