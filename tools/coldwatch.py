#!/usr/bin/env python3
"""Does +Drive initialisation ever run on a FRESH boot, or only look like it?

Resuming gui.snap, the firmware shows "FACTORY PROJECT >> +DRIVE..." forever,
issues ZERO eSDHC commands over 100M instructions, never runs the job pump,
and has exactly one task in the ready list. That is the documented staleness
trap: unblock force-satisfies a sem_pend on the way IN and can never satisfy a
wait that is already blocked, so any worker that parked BEFORE the snapshot
was saved stays parked on every resume of it.

If that is all this is, a fresh boot -- where every wait is seen on the way in
-- should run the job pump and issue real storage traffic, and the fix is
simply to save the snapshot at a better moment. If a fresh boot behaves the
same way, the +Drive path is blocked by something structural and no snapshot
will fix it.

Uses the same intro policy as tools/introboot.py, because mk1 needs PIT3
delivered during the intro and its frame semaphore left alone.
"""
import argparse
import collections
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panel, symbols
from emu.dtim import Dtims, Timers
from emu.pit import Pits

INTRO_DONE = 0x4006CB94
PIT3_VECTOR_SLOT = 0x40000340
POST_ISR = 0x400E5CEC


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot',
                    default='snapshots/Digitakt_OS1.53/boot400M.snap')
    ap.add_argument('--budget', type=lambda s: int(s, 0), default=500_000_000)
    ap.add_argument('--chunk', type=lambda s: int(s, 0), default=20_000_000)
    ap.add_argument('--out', default='', help='save a snapshot here at the end')
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    if prof.frame_sem is None:
        raise SystemExit('frame_sem unresolved')

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

    hits = collections.Counter()
    for name in ('job_pump', 'mainloop'):
        addr = getattr(prof, name, None)
        if addr:
            at(addr, (lambda n: (lambda *_: hits.__setitem__(n, hits[n] + 1)))(name))

    esdhc = ev.get('esdhc')
    log = getattr(esdhc, 'log', None) if esdhc else None
    cap = panel.Capture(at, diff_addr=getattr(prof, 'panel_diff', None),
                        front_addr=prof.fb_front)

    print('%6s %-8s %-8s %-9s %-9s %-10s %s'
          % ('instr', 'jobs', 'mainloop', 'SD cmds', 'overlay', 'frames',
             'phase'))
    total = 0
    seen = 0
    while total < args.budget:
        pc, _e, _w = longrun.spin(m, pc, args.chunk, pits=pits)
        total += args.chunk
        frames = cap.frames[seen:]
        seen = len(cap.frames)
        lits = [len(panel.lit(b)) for b in frames]
        overlay = sum(1 for n in lits if n <= 1200)
        slot = struct.unpack('>I', m.uc.mem_read(PIT3_VECTOR_SLOT, 4))[0]
        print('%5dM %-8d %-8d %-9s %-9d %-10d %s'
              % (total // 1_000_000, hits['job_pump'], hits['mainloop'],
                 len(log) if log is not None else '?', overlay, len(frames),
                 'OS' if slot == POST_ISR else
                 ('post-intro' if state['done'] else 'intro')), flush=True)

    print('')
    if log is not None:
        kinds = collections.Counter(idx for idx, _a in log)
        print('eSDHC commands: %d total  %s'
              % (len(log), ', '.join('CMD%d x%d' % (i, n)
                                     for i, n in kinds.most_common(8))))
        print('card overlay: %d byte(s) written' % len(esdhc.card.overlay))
    print('job_pump=%d mainloop=%d' % (hits['job_pump'], hits['mainloop']))
    if log is not None:
        print('')
        print('FIRST 12 commands:')
        for idx, a_ in list(log[:12]):
            print('  CMD%-3d arg=0x%08x' % (idx, a_))
        print('LAST 24 commands (this is where it stops):')
        for idx, a_ in list(log[-24:]):
            print('  CMD%-3d arg=0x%08x' % (idx, a_))

    if args.out:
        from emu.snapshot import save
        ev['claim_checkpoint_component']('timers', pits)
        os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
        save(m, args.out, extra={'n': total, 'tasks': dict(ev.get('tasks', {}))},
             components=ev['checkpoint_components'],
             manifest=ev.get('checkpoint_manifest'))
        print('saved %s' % args.out)


if __name__ == '__main__':
    main()
