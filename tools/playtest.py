#!/usr/bin/env python3
"""Press PLAY and show the sequencer running.

A single screenshot cannot distinguish "the transport is running" from "the
transport is stopped on a screen that happens to look busy". A run of frames
can: if the sequencer is advancing, successive frames DIFFER, and they differ
in a bounded, repeating way as the playhead steps.

So this takes a baseline run of frames with the transport stopped, presses
PLAY, takes another run, and reports how many frames changed in each. Stopped
should be mostly static; running should not be.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panel, panelin, symbols
from emu.dtmap import names as table_names, wire_for
from emu.uiresume import open_snapshot


def code_to_wire(code):
    """-> (channel, bit) from the MEASURED mk1 wiring (emu/dtmap.py)."""
    wire = wire_for(code)
    if wire is None:
        raise SystemExit('control code %d is not on this panel' % code)
    return wire


def frames(m, prof, pits, pc, cap, n, step, tag, outdir):
    """-> (pc, distinct frame list). Uses UNTORN frames only.

    panel.read() at an arbitrary instant samples the framebuffer mid-flush and
    returns a torn frame, which shows up as wild swings in the lit-pixel count
    (1702, 410, 1702, 939...) whether or not anything is actually animating.
    panel.Capture hooks the diff's ENTRY, where emu/panel.py guarantees the
    front buffer is a complete just-rendered frame.
    """
    start = len(cap.frames)
    for _ in range(n):
        pc, _e, _w = longrun.spin(m, pc, step, pits=pits)
    got = cap.frames[start:]
    # collapse consecutive duplicates: that is what "the screen changed" means
    distinct = []
    for buf in got:
        if not distinct or buf != distinct[-1]:
            distinct.append(buf)
    if outdir:
        for i, buf in enumerate(distinct[:12]):
            panel.write_png(buf, os.path.join(outdir, '%s-%02d.png' % (tag, i)),
                            scale=6)
    print('   %d untorn frame(s), %d distinct; lit: %s'
          % (len(got), len(distinct), [len(panel.lit(b)) for b in distinct[:12]]),
          flush=True)
    return pc, distinct


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--press', action='append', default=[],
                    help='control NAME to tap before the running run')
    ap.add_argument('--frames', type=int, default=8)
    ap.add_argument('--step', type=lambda s: int(s, 0), default=6_000_000)
    ap.add_argument('--out', default='out/play')
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)

    # code -> name straight out of the image, then inverted
    names = table_names(img)

    m, ev, st, pc, inq, at, pits = open_snapshot(args.snapshot, args.syx, prof)
    os.makedirs(args.out, exist_ok=True)
    cap = panel.Capture(at, diff_addr=getattr(prof, 'panel_diff', None),
                        front_addr=prof.fb_front)

    print('== baseline: transport stopped ==', flush=True)
    pc, stopped = frames(m, prof, pits, pc, cap, args.frames, args.step,
                         'stopped', args.out)

    for spec in args.press:
        code = names.get(spec.strip().upper())
        if code is None:
            raise SystemExit('unknown control %r' % spec)
        ch, bit = code_to_wire(code)
        print('\n== press %s (my code %d -> channel %d bit %d, firmware code %d) =='
              % (spec, code, ch, bit, ch * 8 + bit), flush=True)
        pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 1 << bit))
        pc, _e, _w = longrun.spin(m, pc, 4_000_000, pits=pits)
        pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 0))
        pc, _e, _w = longrun.spin(m, pc, 4_000_000, pits=pits)

    print('\n== after the press ==', flush=True)
    pc, running = frames(m, prof, pits, pc, cap, args.frames, args.step,
                         'running', args.out)
    print('')
    print('== verdict ==')
    print('   distinct frames while stopped : %d' % len(stopped))
    print('   distinct frames after press   : %d' % len(running))


if __name__ == '__main__':
    main()
