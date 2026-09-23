#!/usr/bin/env python3
"""What is the +Drive actually doing? Dump the eSDHC command traffic.

The firmware sits on "FACTORY PROJECT >> +DRIVE..." indefinitely -- measured
over 250M instructions with no sign of converging -- while the job pump never
runs once. Two very different things look like that from the outside:

  progress   it really is writing factory projects and is merely slow, in
             which case the block arguments climb steadily
  stuck      it issues the same command over and over and never gets the
             answer it wants, in which case the same argument repeats

emu/esdhc.py's controller keeps a log of (command index, argument) in issue
order, which distinguishes them directly. CMD25 is WRITE_MULTIPLE_BLOCK and
CMD18 is READ_MULTIPLE_BLOCK, both sector-addressed, so a climbing argument
is real progress through the drive.

Also reports the card overlay's size: that is the sparse dict of bytes the
firmware has actually written, so it says whether writes are landing at all.
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, symbols
from emu.uiresume import open_snapshot

GUI_FLAGS = dict(unblock=True, softfloat=True, bitmap=True, dsp=True)

NAMES = {0: 'GO_IDLE', 1: 'SEND_OP_COND', 2: 'ALL_SEND_CID', 6: 'SWITCH',
         7: 'SELECT_CARD', 8: 'SEND_EXT_CSD', 12: 'STOP_TRANSMISSION',
         13: 'SEND_STATUS', 16: 'SET_BLOCKLEN', 17: 'READ_SINGLE',
         18: 'READ_MULTIPLE', 23: 'SET_BLOCK_COUNT', 24: 'WRITE_SINGLE',
         25: 'WRITE_MULTIPLE', 19: 'BUSTEST_W', 14: 'BUSTEST_R'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', default='snapshots/Digitakt_OS1.53/gui.snap')
    ap.add_argument('--budget', type=lambda s: int(s, 0), default=120_000_000)
    ap.add_argument('--chunk', type=lambda s: int(s, 0), default=20_000_000)
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    flags = dict(GUI_FLAGS)
    if prof.frame_sem is not None:
        flags['unblock_except'] = (prof.frame_sem,)
    m, ev, st, pc, inq, at, pits = open_snapshot(
        args.snapshot, args.syx, prof, **flags)

    esdhc = ev.get('esdhc')
    if esdhc is None:
        raise SystemExit('no esdhc model on this build')
    log = getattr(esdhc, 'log', None)
    if log is None:
        raise SystemExit('this Esdhc has no command log')

    print('card overlay at start: %d byte(s) written'
          % len(esdhc.card.overlay))
    start = len(log)
    total = 0
    while total < args.budget:
        pc, _e, _w = longrun.spin(m, pc, args.chunk, pits=pits)
        total += args.chunk
        new = list(log[start:])
        start = len(log)
        kinds = collections.Counter(idx for idx, _arg in new)
        print('%5dM  %-4d command(s)  overlay=%-9d %s'
              % (total // 1_000_000, len(new), len(esdhc.card.overlay),
                 ', '.join('%s x%d' % (NAMES.get(i, 'CMD%d' % i), n)
                           for i, n in kinds.most_common(5)) or '(none)'),
              flush=True)

    print('')
    tail = list(log[-24:])
    print('last %d command(s), newest last:' % len(tail))
    for idx, arg in tail:
        print('  %-16s arg=0x%08x  (sector %d)'
              % (NAMES.get(idx, 'CMD%d' % idx), arg, arg))

    args_by_cmd = collections.defaultdict(list)
    for idx, arg in log:
        args_by_cmd[idx].append(arg)
    print('')
    print('%-16s %-8s %-12s %s' % ('command', 'count', 'distinct args',
                                   'verdict'))
    for idx, vals in sorted(args_by_cmd.items()):
        distinct = len(set(vals))
        verdict = ''
        if len(vals) > 20:
            verdict = ('REPEATING one argument -- stuck'
                       if distinct <= 2 else 'arguments advance -- progress')
        print('%-16s %-8d %-12d %s'
              % (NAMES.get(idx, 'CMD%d' % idx), len(vals), distinct, verdict))


if __name__ == '__main__':
    main()
