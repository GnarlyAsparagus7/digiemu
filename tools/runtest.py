#!/usr/bin/env python3
"""Does the SCREEN change while the transport runs? Correct key this time.

The earlier screen test pressed code 25, which the panel-test table calls
"PLAY" but the runtime treats as trig 2. And posSrc/posNext were never
confirmed to be the playhead -- they came from a LIKELY-confidence finding
and a partial read of the ISR. So both legs of "the sequencer is not running"
rested on assumptions.

This avoids both. It presses the key measured to arm the transport (code 10)
and compares untorn frames before and after. If the sequencer advances
anything the UI draws -- a playhead, a step cursor, a position readout --
distinct frames go up. It does not matter which RAM word holds the position.

Untorn frames only: panel.read() at an arbitrary instant samples mid-flush
and returns torn frames whose lit counts swing wildly whether or not
anything is animating. panel.Capture hooks the diff's entry.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panel, panelin, symbols
from emu.dtmap import wire_for
from emu.uiresume import open_snapshot

PLAY, STOP = 10, 11


def tap(m, prof, pits, pc, code, step):
    ch, bit = wire_for(code)
    pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 1 << bit))
    pc, _e, _w = longrun.spin(m, pc, step, pits=pits)
    pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 0))
    pc, _e, _w = longrun.spin(m, pc, step, pits=pits)
    return pc


def window(m, pits, pc, cap, n, step, tag, outdir):
    start = len(cap.frames)
    for _ in range(n):
        pc, _e, _w = longrun.spin(m, pc, step, pits=pits)
    got = cap.frames[start:]
    distinct = []
    for buf in got:
        if not distinct or buf != distinct[-1]:
            distinct.append(buf)
    lits = [len(panel.lit(b)) for b in distinct[:14]]
    print('  %-8s %3d untorn frame(s), %3d distinct; lit: %s'
          % (tag, len(got), len(distinct), lits), flush=True)
    if outdir:
        os.makedirs(outdir, exist_ok=True)
        for i, buf in enumerate(distinct[:8]):
            panel.write_png(buf, os.path.join(outdir, '%s-%02d.png' % (tag, i)),
                            scale=6)
    return pc, distinct


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--frames', type=int, default=16)
    ap.add_argument('--step', type=lambda s: int(s, 0), default=5_000_000)
    ap.add_argument('--out', default='out/runtest')
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    m, ev, st, pc, inq, at, pits = open_snapshot(args.snapshot, args.syx, prof)
    cap = panel.Capture(at, diff_addr=getattr(prof, 'panel_diff', None),
                        front_addr=prof.fb_front)

    pc, _e, _w = longrun.spin(m, pc, 5_000_000, pits=pits)
    print('== transport stopped ==')
    pc, stopped = window(m, pits, pc, cap, args.frames, args.step,
                         'stopped', args.out)

    print('')
    print('== press PLAY (code %d -> ch %d bit %d) ==' % (PLAY, *wire_for(PLAY)))
    pc = tap(m, prof, pits, pc, PLAY, 5_000_000)
    pc, running = window(m, pits, pc, cap, args.frames, args.step,
                         'running', args.out)

    print('')
    print('== press STOP (code %d) ==' % STOP)
    pc = tap(m, prof, pits, pc, STOP, 5_000_000)
    pc, halted = window(m, pits, pc, cap, args.frames, args.step,
                        'halted', args.out)

    print('')
    print('== verdict ==')
    print('  distinct frames  stopped=%d  running=%d  halted=%d'
          % (len(stopped), len(running), len(halted)))
    if len(running) > len(stopped) and len(running) > len(halted):
        print('  THE UI ANIMATES WHILE THE TRANSPORT RUNS and settles when')
        print('  stopped -- the sequencer is running.')
    elif len(running) > 1:
        print('  the screen changes while running, but not more than when')
        print('  stopped -- inconclusive.')
    else:
        print('  the screen is static while the transport is armed.')


if __name__ == '__main__':
    main()
