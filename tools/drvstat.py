#!/usr/bin/env python3
"""Has the firmware already written to the +Drive?

The eSDHC model keeps host writes in a sparse `overlay` dict consulted ahead
of the backing store, so with image=None the overlay IS the writable medium.
If the firmware initialised a blank drive during boot -- it has an
"INITIALIZING +DRIVE..." string, referenced from the storage region -- then
those writes are already sitting in that overlay and a formatted image can be
exported without driving a single menu.

Reports how much has been written, where, and dumps the first populated
sectors so the filesystem can be identified.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, symbols
from emu.uiresume import open_snapshot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--run', type=lambda s: int(s, 0), default=20_000_000)
    ap.add_argument('--export', default='')
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    m, ev, st, pc, inq, at, pits = open_snapshot(args.snapshot, args.syx, prof)

    # dspboot (cold boot) hangs the model off the machine as m.esdhc; longrun
    # (resume) registers it as ev['esdhc'] instead. Accept either.
    esdhc = getattr(m, 'esdhc', None) or ev.get('esdhc')
    if esdhc is None:
        raise SystemExit('no eSDHC model: keys=%s' % sorted(ev)[:20])
    card = getattr(esdhc, 'card', esdhc)
    ov = getattr(card, 'overlay', None)
    if ov is None:
        raise SystemExit('the eSDHC model exposes no overlay')

    print('capacity   : %s blocks (%.2f GB)'
          % (getattr(card, 'blocks', '?'),
             (getattr(card, 'blocks', 0) or 0) * 512 / 1e9))
    print('backing img: %s' % ('none (overlay only)' if getattr(card, 'image', None) is None else 'present'))
    print('overlay    : %d byte(s) written at boot' % len(ov))

    pc, ex, why = longrun.spin(m, pc, args.run, pits=pits)
    print('after %dM more instructions: overlay = %d byte(s)'
          % (args.run // 1_000_000, len(ov)))

    if not ov:
        print('')
        print('nothing has been written: the firmware has not touched the drive.')
        return

    keys = sorted(ov)
    lo, hi = keys[0], keys[-1]
    print('')
    print('written range: 0x%x .. 0x%x  (sector %d .. %d)'
          % (lo, hi, lo // 512, hi // 512))
    sectors = sorted({k // 512 for k in ov})
    print('touched sectors: %d distinct; first few: %s'
          % (len(sectors), sectors[:12]))

    for sec in sectors[:3]:
        base = sec * 512
        row = bytes(ov.get(base + i, 0) for i in range(512))
        nz = [i for i, c in enumerate(row) if c]
        print('')
        print('sector %d (offset 0x%x): %d non-zero byte(s), last at +0x%x'
              % (sec, base, len(nz), nz[-1] if nz else 0))
        # Print only the lines that carry data; a 512-byte hexdump of mostly
        # zeros hides the structure it is meant to show.
        for i in range(0, 512, 16):
            chunk = row[i:i + 16]
            if not any(chunk):
                continue
            txt = ''.join(chr(c) if 32 <= c < 127 else '.' for c in chunk)
            print('   %04x  %-48s %s'
                  % (i, ' '.join('%02x' % c for c in chunk), txt))

    if args.export:
        # Write a sparse image: every touched sector, zeros elsewhere.
        size = (max(sectors) + 1) * 512
        buf = bytearray(size)
        for k, v in ov.items():
            if k < size:
                buf[k] = v
        with open(args.export, 'wb') as fh:
            fh.write(buf)
        print('')
        print('exported %d byte(s) -> %s' % (size, args.export))


if __name__ == '__main__':
    main()
