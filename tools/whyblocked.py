#!/usr/bin/env python3
"""What is the firmware waiting for once +Drive setup stalls?

Established: on a fresh boot the firmware erases 514 groups from sector
0x80000, writes two blocks, reads 2MB back (2048 x CMD18, sectors 0x1000 to
0x1ffe, ending exactly on the 0x2000 boundary) and then issues no further
storage commands at all -- while redrawing "FACTORY PROJECT >> +DRIVE..."
over the main page roughly twice a second, forever.

With unblock=True almost every sem_pend is force-satisfied on the way in, so
the waits that can actually block are a short list: the semaphores in the skip
set (the intro frame semaphore and the display module's), and the `recheck`
call sites that re-test a condition and loop. A pend that keeps recurring at
one call site is therefore exactly what the system is spinning on.

So this histograms (return address, semaphore) for every pend in a window,
and reports the coprocessor port's counters alongside -- if the job worker is
wedged on the 0x8C000000 ready line, as emu/gui.py warns it can be, the
port's poll count is where that shows.
"""
import argparse
import collections
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unicorn.m68k_const import UC_M68K_REG_A7

from emu import config, longrun, symbols
from emu.dtim import Dtims, Timers
from emu.pit import Pits

INTRO_DONE = 0x4006CB94


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot',
                    default='snapshots/Digitakt_OS1.53/boot400M.snap')
    ap.add_argument('--settle', type=lambda s: int(s, 0), default=120_000_000)
    ap.add_argument('--window', type=lambda s: int(s, 0), default=20_000_000)
    ap.add_argument('--top', type=int, default=14)
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)

    m, ev, st, pc, inq, at = longrun.build(
        args.snapshot, syx=args.syx, unblock=True, softfloat=True,
        bitmap=True, dsp=True, unblock_except=(prof.frame_sem,),
        deferred_components=('timers',))

    pit = Pits(m, channels=(3,), hold=False)
    dtim = Dtims(m, channels=(1, 3), hold=True)
    pits = Timers(pit, dtim)
    skip = ev.get('unblock_skip')
    state = {'done': 0}

    def handover(*_a):
        if state['done']:
            return
        state['done'] = 1
        if skip is not None:
            skip.add(prof.frame_sem)
        pit.channels = (3, 2, 0)
        pits.release()

    at(INTRO_DONE, handover)

    print('settling %dM instructions...' % (args.settle // 1_000_000),
          flush=True)
    done = 0
    while done < args.settle:
        pc, _e, _w = longrun.spin(m, pc, 20_000_000, pits=pits)
        done += 20_000_000

    pends = collections.Counter()
    recording = {'on': True}

    def on_pend(uc, a, s_, d):
        if not recording['on']:
            return
        try:
            sp = uc.reg_read(UC_M68K_REG_A7)
            ret, sem = struct.unpack('>II', uc.mem_read(sp, 8))
            pends[(ret, sem)] += 1
        except Exception:
            pass

    at(prof.sem_pend, on_pend)
    if getattr(prof, 'pend_b', None):
        at(prof.pend_b, on_pend)

    print('recording pends over %dM instructions...'
          % (args.window // 1_000_000), flush=True)
    pc, _e, _w = longrun.spin(m, pc, args.window, pits=pits)
    recording['on'] = False

    fifo = ev.get('dsp') or ev.get('fifo')
    print('')
    if fifo is not None:
        print('coprocessor port: polls=%s words=%s bursts=%s'
              % (getattr(fifo, 'polls', '?'), getattr(fifo, 'words', '?'),
                 getattr(fifo, 'bursts', '?')))
    else:
        print('coprocessor port: no model exposed on ev '
              '(keys: %s)' % ', '.join(sorted(k for k in ev if isinstance(k, str)))[:200])

    named = {prof.frame_sem: 'frame_sem'}
    if prof.display_sem:
        named[prof.display_sem] = 'display_sem'
    for n in ('sd_cmd_sem', 'sd_data_sem', 'sd_dma_sem', 'completion_sem'):
        v = getattr(prof, n, None)
        if isinstance(v, int):
            named[v] = n
    skipped = set(skip or ())

    print('')
    print('%-12s %-12s %-9s %-8s %s'
          % ('pend site', 'semaphore', 'count', 'skipped', 'name'))
    for (ret, sem), n in pends.most_common(args.top):
        print('0x%08x   0x%08x   %-9d %-8s %s'
              % (ret, sem, n, 'YES' if sem in skipped else '',
                 named.get(sem, '')))
    print('')
    print('%d pend(s) total at %d distinct site(s)'
          % (sum(pends.values()), len(pends)))


if __name__ == '__main__':
    main()
