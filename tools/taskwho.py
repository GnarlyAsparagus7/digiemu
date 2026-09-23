#!/usr/bin/env python3
"""Which task_create sites actually run, and which never do?

The +Drive overlay is redrawn forever and the job pump never runs once. Nothing
is blocked waiting -- a pend histogram over a 20M window finds only the RTOS
tick, the main queue and the display semaphore -- so the +Drive state machine
is not stuck on anything, it has no driver.

emu/gui.py calls the job worker "the priority-3 job worker", and no priority-3
task appears in the task inventory. There is a task_create call site at
0x4008E0B0, in the same region as job_pump (0x4008E1C4) and its only caller
(0x4008E588), so that site is what creates the pool.

This hooks all 16 task_create sites and reports which execute. A site that
never fires is a task that was never created, which is a very different
problem from a task that was created and then blocked.
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, symbols
from emu.dtim import Dtims, Timers
from emu.pit import Pits

INTRO_DONE = 0x4006CB94
JOB_POOL_SITE = 0x4008E0B0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot',
                    default='snapshots/Digitakt_OS1.53/boot400M.snap')
    ap.add_argument('--budget', type=lambda s: int(s, 0), default=200_000_000)
    ap.add_argument('--chunk', type=lambda s: int(s, 0), default=20_000_000)
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
    done = {'v': 0}

    def handover(*_a):
        if done['v']:
            return
        done['v'] = 1
        if skip is not None:
            skip.add(prof.frame_sem)
        pit.channels = (3, 2, 0)
        pits.release()

    at(INTRO_DONE, handover)

    sites = list(prof.task_create_sites or ())
    hits = collections.Counter()
    for s in sites:
        at(s, (lambda a_: (lambda *_: hits.__setitem__(a_, hits[a_] + 1)))(s))

    # Also the job pool's enclosing routine and the pump itself.
    extra = {0x4008E0B0: 'task_create in the job-pool region',
             0x4008E1C4: 'job_pump', 0x4008E588: 'the only jsr to job_pump'}
    for a_, _n in extra.items():
        if a_ not in sites:
            at(a_, (lambda x: (lambda *_: hits.__setitem__(x, hits[x] + 1)))(a_))

    total = 0
    while total < args.budget:
        pc, _e, _w = longrun.spin(m, pc, args.chunk, pits=pits)
        total += args.chunk
        print('  %4dM  %d of %d task_create sites have fired'
              % (total // 1_000_000,
                 sum(1 for s in sites if hits[s]), len(sites)), flush=True)

    print('')
    print('%-12s %-8s %s' % ('site', 'hits', 'note'))
    for s in sorted(set(sites) | set(extra)):
        note = extra.get(s, '')
        if s in sites and not hits[s]:
            note = (note + '  <- NEVER RAN').strip()
        print('0x%08X   %-8d %s' % (s, hits[s], note))
    print('')
    if hits.get(JOB_POOL_SITE):
        print('The job pool site DID run (%d time(s)).' % hits[JOB_POOL_SITE])
    else:
        print('The job pool site at 0x%08X NEVER RAN -- the worker pool is '
              'not created at all, which is why the pump never pumps.'
              % JOB_POOL_SITE)


if __name__ == '__main__':
    main()
