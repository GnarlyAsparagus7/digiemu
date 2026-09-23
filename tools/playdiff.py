#!/usr/bin/env python3
"""Where exactly does the screen change when PLAY is pressed?

The lit-pixel count went 1873 -> 1880 after PLAY: seven pixels, which is
either a transport indicator lighting up or nothing meaningful. A count
cannot tell those apart; the location can.

The framebuffer is PACKED (1024 bytes for 128x64), so it cannot be indexed
per pixel -- panel.ascii_art unpacks it into rows, and rows diff cleanly.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panel, panelin, symbols
from emu.dtmap import names as table_names, wire_for
from emu.uiresume import open_snapshot

W = 128


def rows(buf):
    art = panel.ascii_art(buf)
    return art.splitlines() if isinstance(art, str) else list(art)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--press', default='PLAY')
    ap.add_argument('--step', type=lambda s: int(s, 0), default=8_000_000)
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    names = table_names(img)
    m, ev, st, pc, inq, at, pits = open_snapshot(args.snapshot, args.syx, prof)
    cap = panel.Capture(at, diff_addr=getattr(prof, 'panel_diff', None),
                        front_addr=prof.fb_front)

    pc, _e, _w = longrun.spin(m, pc, args.step, pits=pits)
    before = cap.frames[-1] if cap.frames else None

    code = names[args.press.upper()]
    ch, bit = wire_for(code)
    print('press %s (code %d -> channel %d bit %d)' % (args.press, code, ch, bit))
    pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 1 << bit))
    pc, _e, _w = longrun.spin(m, pc, args.step, pits=pits)
    pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 0))
    pc, _e, _w = longrun.spin(m, pc, args.step, pits=pits)
    after = cap.frames[-1] if cap.frames else None

    if before is None or after is None:
        raise SystemExit('no frames captured')

    ra, rb = rows(before), rows(after)
    diff = [(x, y) for y in range(min(len(ra), len(rb)))
            for x in range(min(len(ra[y]), len(rb[y])))
            if ra[y][x] != rb[y][x]]
    print('')
    print('changed pixels: %d' % len(diff))
    if not diff:
        print('the screen is byte-identical: PLAY produced no visible change')
        return
    xs = [p[0] for p in diff]
    ys = [p[1] for p in diff]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    print('bounding box: x %d..%d, y %d..%d  (panel 128x64)' % (x0, x1, y0, y1))

    pad = 3
    xa, xb = max(0, x0 - pad), min(W, x1 + pad + 1)
    print('')
    print('region      before          ->  after')
    for y in range(max(0, y0 - pad), min(len(ra), y1 + pad + 1)):
        print('  y=%-3d %-28s   %s' % (y, ra[y][xa:xb], rb[y][xa:xb]))


if __name__ == '__main__':
    main()
