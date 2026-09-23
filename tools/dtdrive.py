#!/usr/bin/env python3
"""Drive the panel headlessly and show what the firmware drew.

Resumes a snapshot, lets it settle, then presses a sequence of controls named
the way the FIRMWARE names them and dumps the OLED after each step, as ASCII
and optionally as PNG. No GUI and no X server, so it works over a pipe and in
CI, which is what makes it usable as a check rather than a demo.

Controls are named, not numbered: codes are looked up in the image's own
control table via emu/symbols.py, so this script says PLAY and means whatever
code this build calls PLAY.

    python tools/dtdrive.py --syx F.syx --snapshot S.snap \
        --press PLAY --press 1 --hold FUNC+SRC --png out/

A `+` joins controls into a CHORD: every one of them is held down together,
which the wire expresses natively because each message carries a whole
channel's eight-bit state mask rather than an event.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panel, panelin, symbols
from emu.dtmap import names as table_names, wire_for
from emu.uiresume import open_snapshot


def code_to_wire(code):
    """-> (channel, bit) for a control code, from the MEASURED mk1 wiring.

    Not arithmetic: this panel's matrix is not scanned in code order, so
    channel*8+bit addresses a different key than the one you named. See
    emu/dtmap.py.
    """
    wire = wire_for(code)
    if wire is None:
        raise SystemExit('control code %d is not on this panel' % code)
    return wire


def press_bytes(masks, codes, down):
    """-> wire bytes setting/clearing `codes`, keeping other held bits."""
    touched = set()
    for code in codes:
        channel, bit = code_to_wire(code)
        if down:
            masks[channel] = masks.get(channel, 0) | (1 << bit)
        else:
            masks[channel] = masks.get(channel, 0) & ~(1 << bit)
        touched.add(channel)
    out = b''
    for channel in sorted(touched):
        out += panelin.encode_buttons(channel, masks.get(channel, 0))
    return out


def dump(m, fb_front, label, png_dir=None, index=0):
    buf = panel.read(m, fb_front) if fb_front else None
    if buf is None:
        print('  [%s] no framebuffer (panel_diff/fb_front unresolved)' % label)
        return 0
    n = len(panel.lit(buf))
    print('\n=== %s -- %d lit pixels ===' % (label, n))
    art = panel.ascii_art(buf)
    # ascii_art returns the rows, not a blob; printing the list itself gives
    # a Python repr with every row quoted, which is unreadable in a log.
    print(art if isinstance(art, str) else '\n'.join(art))
    if png_dir:
        os.makedirs(png_dir, exist_ok=True)
        path = os.path.join(png_dir, '%02d-%s.png'
                            % (index, label.replace(' ', '_').replace('+', '-')))
        panel.write_png(buf, path, scale=6)
        print('  -> %s' % path)
    return n


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--settle', type=lambda s: int(s, 0), default=40_000_000,
                    help='instructions to run before the first capture')
    ap.add_argument('--step', type=lambda s: int(s, 0), default=12_000_000,
                    help='instructions to run after each press')
    ap.add_argument('--press', action='append', default=[],
                    help='control name to tap; A+B for a chord. Repeatable.')
    ap.add_argument('--hold', action='append', default=[],
                    help='control name to press and LEAVE held. Repeatable.')
    ap.add_argument('--png', help='directory to write frame PNGs into')
    args = ap.parse_args(argv)

    img = open(config.main_image(), 'rb').read()
    profile = symbols.resolve(img, load_addr=0x40000400)
    names = table_names(img)
    print('control table: %d names' % len(names))

    def resolve_names(spec):
        codes = []
        for part in spec.split('+'):
            key = part.strip().upper()
            if key not in names:
                raise SystemExit('unknown control %r; have e.g. %s'
                                 % (part, ', '.join(sorted(names)[:12])))
            codes.append(names[key])
        return codes

    plan = [('hold', s) for s in args.hold] + [('tap', s) for s in args.press]

    # A Timers is not optional -- longrun.spin services interrupt sources
    # only when handed one -- and a mid-run snapshot needs its SAVED cadence
    # rather than fresh sources. open_snapshot does both.
    m, ev, st, pc, inq, at, pits = open_snapshot(
        args.snapshot, args.syx, profile)
    fb_front = getattr(profile, 'fb_front', None)

    pc, executed, why = longrun.spin(m, pc, args.settle, pits=pits)
    print('settled: %d instructions, stop=%s' % (executed, why))
    shots = 0
    dump(m, fb_front, 'boot', args.png, shots)

    masks = {}
    for kind, spec in plan:
        codes = resolve_names(spec)
        # panelin.feed writes the ring, advances DADDR and raises the RX
        # vector, so the firmware's own ISR drains it -- the same path the
        # panel MCU's DMA uses. Appending to `inq` would skip all of that.
        pc = panelin.feed(m, profile, press_bytes(masks, codes, True))
        pc, _e, _w = longrun.spin(m, pc, args.step, pits=pits)
        if kind == 'tap':
            pc = panelin.feed(m, profile, press_bytes(masks, codes, False))
            pc, _e, _w = longrun.spin(m, pc, args.step, pits=pits)
        shots += 1
        dump(m, fb_front, spec, args.png, shots)

    print('\ndone: %d capture(s)' % (shots + 1))


if __name__ == '__main__':
    main()
