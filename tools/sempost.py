#!/usr/bin/env python3
"""Who posts which semaphore? And is the job worker's ever posted?

Established by reading the parked stack of the priority-2 job worker in
boot400M: it is blocked in pend_b at 0x4008e1f4, inside job_pump, waiting on
the semaphore at 0x42A3C544 -- the pool object (0x42A3C530) plus 0x14. Its
count reads 0, so no job has ever been queued.

A static scan of the whole image finds exactly ONE site that posts a
semaphore at object+0x14, and it is inside job_pump itself. Either the
submitter computes the address in a register (which that scan cannot see) or
nothing ever posts it. Only a runtime trace tells those apart.

So hook sem_post, record (caller, semaphore) for every call, and say plainly
whether the worker's semaphore is among them.
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
WORKER_SEM = 0x42A3C544          # pool object + 0x14, read from the snapshot
POOL_GUARD = 0x421F9B78


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot',
                    default='snapshots/Digitakt_OS1.53/boot400M.snap')
    ap.add_argument('--budget', type=lambda s: int(s, 0), default=200_000_000)
    ap.add_argument('--chunk', type=lambda s: int(s, 0), default=40_000_000)
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

    # The pool pointer can differ per boot; read it from the live guard.
    try:
        pool = struct.unpack('>I', m.uc.mem_read(POOL_GUARD, 4))[0]
    except Exception:
        pool = 0
    worker_sem = (pool + 0x14) if pool else WORKER_SEM
    print('pool guard 0x%08X -> pool 0x%08X, worker semaphore 0x%08X'
          % (POOL_GUARD, pool, worker_sem))

    posts = collections.Counter()
    by_sem = collections.defaultdict(collections.Counter)
    target = {'n': 0, 'callers': collections.Counter()}

    def on_post(uc, a, s_, d):
        try:
            sp = uc.reg_read(UC_M68K_REG_A7)
            ret, sem = struct.unpack('>II', uc.mem_read(sp, 8))
        except Exception:
            return
        posts[sem] += 1
        by_sem[sem][ret] += 1
        if sem == worker_sem:
            target['n'] += 1
            target['callers'][ret] += 1

    post_addr = getattr(prof, 'sem_post', None) or 0x40001770
    at(post_addr, on_post)

    total = 0
    while total < args.budget:
        pc, _e, _w = longrun.spin(m, pc, args.chunk, pits=pits)
        total += args.chunk
        try:
            cnt = struct.unpack('>i', m.uc.mem_read(worker_sem, 4))[0]
        except Exception:
            cnt = None
        print('  %4dM  %d sem_post call(s), %d distinct semaphore(s); '
              'worker sem posted %d time(s), count=%s'
              % (total // 1_000_000, sum(posts.values()), len(posts),
                 target['n'], cnt), flush=True)

    print('')
    print('%-12s %-8s %s' % ('semaphore', 'posts', 'top callers'))
    for sem, n in posts.most_common(args.top):
        tag = '  <- THE JOB WORKER SEMAPHORE' if sem == worker_sem else ''
        print('0x%08X   %-8d %s%s'
              % (sem, n, ', '.join('0x%08X x%d' % (r, c)
                                   for r, c in by_sem[sem].most_common(3)),
                 tag))
    print('')
    if target['n']:
        print('The worker semaphore IS posted %d time(s), from %s.'
              % (target['n'],
                 ', '.join('0x%08X' % r for r in target['callers'])))
    else:
        print('The worker semaphore 0x%08X is NEVER posted in %dM '
              'instructions. Nothing submits a job, which is why the +Drive '
              'work has no driver.' % (worker_sem, args.budget // 1_000_000))


if __name__ == '__main__':
    main()
