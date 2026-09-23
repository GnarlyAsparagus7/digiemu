#!/usr/bin/env python3
"""Capture what the INTRO draws, which is not where the main OS draws.

The intro paints through `Bitmap::setPixel`; the main OS composes straight
into its own framebuffer and never calls that primitive. Reading the wrong
one of those two shows a blank or stale screen while the firmware is busy
rendering -- see emu/gui.py's note. This watches the setPixel side, counts
the calls, and writes a PNG per capture so "is the firmware alive" has a
picture for an answer rather than a guess.

    python tools/introcap.py --syx F.syx --snapshot S.snap --png out/intro
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panel

W, H = 128, 64


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--step', type=lambda s: int(s, 0), default=25_000_000)
    ap.add_argument('--shots', type=int, default=6)
    ap.add_argument('--png', default='out/intro')
    args = ap.parse_args()

    fb = bytearray(W * H)
    hits = [0]

    def on_pixel(x, y, val, bmp):
        hits[0] += 1
        if 0 <= x < W and 0 <= y < H:
            fb[y * W + x] = 1 if val else 0

    m, ev, st, pc, inq, at = longrun.build(
        args.snapshot, syx=args.syx, bitmap=True, on_pixel=on_pixel)

    os.makedirs(args.png, exist_ok=True)
    for shot in range(args.shots):
        before = hits[0]
        pc, executed, why = longrun.spin(m, pc, args.step)
        lit = sum(fb)
        print('[%d] +%d instrs  setPixel calls %d (+%d)  lit %d  stop=%s'
              % (shot, executed, hits[0], hits[0] - before, lit, why),
              flush=True)
        path = os.path.join(args.png, '%02d.png' % shot)
        panel.write_png(fb, path, scale=6)
        print('    -> %s' % path, flush=True)

    print('\ntotal setPixel calls: %d' % hits[0])
    if hits[0] == 0:
        print('NOTE: zero setPixel calls means the intro is NOT the thing '
              'running -- look at the panel framebuffer instead.')


if __name__ == '__main__':
    main()
