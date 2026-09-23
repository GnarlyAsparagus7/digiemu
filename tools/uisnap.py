#!/usr/bin/env python3
"""Save a snapshot of the firmware AFTER it has reached its user interface.

The cold-boot ladder cannot do this: emu/dspboot.run does not pace PIT3, so a
cold build produces byte-identical snapshots from 280M onward and never gets
through the 180-frame intro. The intro only runs on a RESUME with a Timers
armed, which costs ~720M instructions (~5 minutes) every single time.

Doing that once and saving the result turns every later interaction test into
a second-long resume instead of a five-minute one.

    python tools/uisnap.py --syx F.syx --snapshot boot400M.snap --out ui.snap
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panel, symbols
from emu.snapshot import save
from emu.uiresume import open_snapshot

MAINLOOP_DEFAULT = 0x4000b6e4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--instrs', type=lambda s: int(s, 0), default=760_000_000)
    ap.add_argument('--chunk', type=lambda s: int(s, 0), default=40_000_000)
    ap.add_argument('--gui-flags', action='store_true',
                    help="build with emu/gui.py's flag set so the "
                         'resulting snapshot can be opened by the GUI')
    args = ap.parse_args()
    flags = dict(unblock=True, softfloat=True, bitmap=True,
                 dsp=True) if args.gui_flags else {}

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    fb_front = getattr(prof, 'fb_front', None)
    mainloop = getattr(prof, 'mainloop', None) or MAINLOOP_DEFAULT

    m, ev, st, pc, inq, at, pits = open_snapshot(
        args.snapshot, args.syx, prof, **flags)
    hits = {'mainloop': 0}
    at(mainloop, lambda uc, a, s, d: hits.__setitem__('mainloop',
                                                      hits['mainloop'] + 1))

    done = 0
    while done < args.instrs:
        pc, executed, why = longrun.spin(m, pc, args.chunk, pits=pits)
        done += executed
        buf = panel.read(m, fb_front) if fb_front else None
        lit = len(panel.lit(buf)) if buf else -1
        print('  %5dM  lit=%-5d mainloop=%d  (%s)'
              % (done // 1_000_000, lit, hits['mainloop'], why), flush=True)
        # Stop as soon as the UI is genuinely up: the main loop is spinning
        # AND the panel shows more than the intro logo's 367 pixels.
        if hits['mainloop'] > 100 and lit > 500:
            print('  -> user interface is live', flush=True)
            break

    if not hits['mainloop']:
        raise SystemExit('main loop never ran; not saving a useless snapshot')

    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    # The timers MUST be saved with it. Without them a resume constructs
    # fresh DTIM sources, which repair "stale" guest DTMR registers and so
    # disarm the DTIM3 the firmware set up for itself -- the snapshot then
    # shows a perfect UI that never executes another instruction.
    ev['claim_checkpoint_component']('timers', pits)
    info = save(m, args.out, extra={'n': done, 'tasks': {}},
                components=ev['checkpoint_components'],
                manifest=ev.get('checkpoint_manifest'))
    print('saved %s (%s)' % (args.out, info if isinstance(info, str) else 'ok'))
    if fb_front:
        buf = panel.read(m, fb_front)
        if buf:
            panel.write_png(buf, args.out.replace('.snap', '.png'), scale=6)
            print('screen -> %s' % args.out.replace('.snap', '.png'))


if __name__ == '__main__':
    main()
