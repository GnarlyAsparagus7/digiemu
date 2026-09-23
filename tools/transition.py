#!/usr/bin/env python3
"""Pin down the point where the GUI's throughput drops, and say what changes.

Reproducible in every GUI run: PIT3 stops at exactly 312 ticks and the
reported rate falls from ~9M instructions/sec to ~3.5M in the same chunk.
Profiling the two phases gives 3.9 instructions per basic-block entry before
and 130,966 after, which is not what "the code got slower" looks like -- it is
what "the machine stopped executing and something is crediting instructions
anyway" looks like.

So this measures the things that distinguish those, per chunk, including the
two that earlier tools threw away:

  wall      real seconds for the chunk, so "slower" is measured rather than
            inferred from the GUI's own status line
  why       spin()'s third return value, which says why the slice ended --
            every tool here has been discarding it
  pc/PCSR   where the guest is, and whether PIT3 is still enabled

If the late phase is the firmware idling, wall time per chunk stays low and
`why` changes. If it is real work, wall time per chunk goes up.
"""
import argparse
import collections
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panel, symbols
from emu.uiresume import open_snapshot

GUI_FLAGS = dict(unblock=True, softfloat=True, bitmap=True, dsp=True)
PIT3_BASE = 0xFC08C000
PIT3_VECTOR_SLOT = 0x40000340


def u16(m, a):
    try:
        return struct.unpack('>H', m.uc.mem_read(a, 2))[0]
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', default='snapshots/Digitakt_OS1.53/gui.snap')
    ap.add_argument('--budget', type=lambda s: int(s, 0), default=260_000_000)
    ap.add_argument('--chunk', type=lambda s: int(s, 0), default=10_000_000)
    ap.add_argument('--on-pixel', action='store_true',
                    help='install a Bitmap::setPixel callback the way '
                         'emu/gui.py does -- the one build argument the GUI '
                         'passes that no headless tool here passes')
    ap.add_argument('--ips', type=lambda s: int(s, 0), default=0,
                    help="override every timer source's instructions-per-"
                         'emulated-second, the way emu/gui.py does with '
                         '--post-intro-ips (it applies 4x at instruction 0)')
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    flags = dict(GUI_FLAGS)
    if prof.frame_sem is not None:
        flags['unblock_except'] = (prof.frame_sem,)
    px = {'n': 0}
    if args.on_pixel:
        def on_pixel(x, y, val):
            px['n'] += 1
        flags['on_pixel'] = on_pixel
    m, ev, st, pc, inq, at, pits = open_snapshot(
        args.snapshot, args.syx, prof, **flags)

    hits = collections.Counter()
    for name in ('mainloop', 'job_pump'):
        addr = getattr(prof, name, None)
        if addr:
            at(addr, (lambda n: (lambda *_: hits.__setitem__(n, hits[n] + 1)))(name))

    if args.ips:
        for source in pits.sources:
            source.ips = args.ips
        print('ips -> %d on %d source(s)' % (args.ips, len(pits.sources)))
    pit = pits.sources[0]
    print('%6s %-7s %-9s %-7s %-8s %-12s %-8s %-9s %s'
          % ('instr', 'wall', 'instr/s', 'PIT3', 'PCSR', 'pc', 'mainloop',
             'setPixel', 'why'))
    total = 0
    while total < args.budget:
        t0 = time.time()
        pc, executed, why = longrun.spin(m, pc, args.chunk, pits=pits)
        dt = time.time() - t0
        total += args.chunk
        ticks = getattr(pit, 'fired', None)
        if isinstance(ticks, dict):
            ticks = ticks.get(3, 0)
        elif hasattr(pit, 'fired'):
            try:
                ticks = pit.fired[3]
            except Exception:
                ticks = -1
        else:
            ticks = -1
        print('%5dM %6.2fs %8.2fM %-7s 0x%04x   0x%08x   %-8d %-9d %s'
              % (total // 1_000_000, dt, executed / dt / 1e6 if dt else 0,
                 ticks, u16(m, PIT3_BASE) or 0, pc, hits['mainloop'],
                 px['n'], why), flush=True)


if __name__ == '__main__':
    main()
