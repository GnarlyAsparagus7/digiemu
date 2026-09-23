#!/usr/bin/env python3
"""Did the cold rebuild change anything? Compare old ladder against new.

The prediction being tested, from emu/longrun.py's own comment about the
progress screen's frame semaphore:

    its per-frame pend was being force-satisfied, so the prio-6 task drew
    about 40 frames per real one and starved the prio-2 job worker doing
    +Drive initialization

display_sem was UNRESOLVED on mk1 until this session, so the cold boot that
built the old ladder was faking exactly that pend. If the comment describes
mk1 too, the old ladder's +Drive worker was starved and the new one's is not.

Reports, for each ladder, the things that would change: task count, the job
pool singleton guard, and whether a priority-3 task exists.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu.snapshot import restore

GUARD = 0x421F9B78
LADDERS = [('old', 'snapshots/Digitakt_OS1.53.preRebuild'),
           ('new', 'snapshots/Digitakt_OS1.53')]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rungs', default='60M,120M,200M,280M,400M')
    args = ap.parse_args()

    print('%-6s %-9s %-8s %-12s %s'
          % ('ladder', 'rung', 'tasks', 'pool guard', 'note'))
    for label, root in LADDERS:
        for rung in args.rungs.split(','):
            p = os.path.join(root, 'boot%s.snap' % rung)
            if not os.path.exists(p):
                continue
            try:
                m, extra, _regs = restore(p)
                tasks = extra.get('tasks', {}) or {}
                guard = struct.unpack('>I', m.uc.mem_read(GUARD, 4))[0]
                prios = sorted(t.get('prio') for t in tasks.values()) \
                    if tasks else []
                note = 'prios %s' % prios if prios else \
                    'no task table in this snapshot'
                if 3 in prios:
                    note += '  <- PRIORITY 3 PRESENT (the job worker)'
                print('%-6s %-9s %-8d 0x%08X   %s'
                      % (label, rung, len(tasks), guard, note))
            except Exception as exc:                     # noqa: BLE001
                print('%-6s %-9s FAILED: %s' % (label, rung, exc))
    print('')
    print('A priority-3 task in the new ladder and not the old one would')
    print('confirm the prediction directly.')


if __name__ == '__main__':
    main()
