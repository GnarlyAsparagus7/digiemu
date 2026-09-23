#!/usr/bin/env python3
"""Backtrace every blocked task from its own saved stack.

emu/tasks.py says WHERE each task is parked, which for a blocked task is
always the same couple of addresses -- the resume point after the scheduler's
`trap #0`. That does not say what it is waiting FOR. The call chain does, and
it is sitting on the task's own stack: ColdFire pushes a two-longword
exception frame and every `jsr` before it left a return address.

A longword is treated as a return address only if the instruction just before
it is a real call -- `jsr abs.l` (0x4EB9), `jsr (aN)` (0x4E90..0x4E97),
`jsr d16(aN)` (0x4EA8..0x4EAF) or `bsr` (0x61xx). Scanning for "any value that
looks like a code pointer" instead produces a stack full of plausible-looking
garbage, which is worse than no backtrace at all.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, symbols, tasks
from emu.snapshot import restore

LOAD = 0x40000400


def is_call_site(img, addr):
    """-> a description if a call instruction ends exactly at `addr`."""
    for back, test in ((6, lambda w: w == 0x4EB9),          # jsr abs.l
                       (2, lambda w: 0x4E90 <= w <= 0x4E97),  # jsr (aN)
                       (4, lambda w: 0x4EA8 <= w <= 0x4EAF),  # jsr d16(aN)
                       (2, lambda w: (w & 0xFF00) == 0x6100)):  # bsr.w/.b
        site = addr - back
        off = site - LOAD
        if off < 0 or off + 2 > len(img):
            continue
        w = struct.unpack_from('>H', img, off)[0]
        if test(w):
            if w == 0x4EB9 and off + 6 <= len(img):
                tgt = struct.unpack_from('>I', img, off + 2)[0]
                return 'jsr 0x%08x' % tgt
            return 'call (0x%04x)' % w
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('snapshot')
    ap.add_argument('--depth', type=int, default=160)
    args = ap.parse_args()

    m, extra, regs = restore(args.snapshot)
    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=LOAD)

    named = {}
    for name in ('sem_pend', 'pend_b', 'queue_recv', 'queue_send', 'sem_post',
                 'task_start', 'mainloop', 'job_pump', 'tick_dispatch',
                 'display_wait', 'pump_wait', 'sleep_pend', 'pend_call'):
        v = getattr(prof, name, None)
        if v:
            named[v] = name

    cur = tasks.u32(m, prof.current_tcb)
    # The ready list holds only what is runnable -- one node here -- so the
    # blocked tasks, which are the interesting ones, come from the snapshot's
    # own recorded task table the way emu/tasks.py reads it.
    recorded = sorted((extra.get('tasks') or {}).values(),
                      key=lambda i: i['prio'])

    for info in recorded:
        tcb = info['tcb']
        if tcb == cur:
            print('\n=== TCB 0x%08x  prio %d  entry 0x%08x  RUNNING ==='
                  % (tcb, info['prio'], info['entry']))
            continue
        sp = tasks.u32(m, tcb + tasks.A7_OFF)
        pc = tasks.u32(m, sp + 4) if sp else None
        print('\n=== TCB 0x%08x  prio %d  entry 0x%08x  a7=0x%08x  pc=%s ==='
              % (tcb, info['prio'], info['entry'], sp or 0,
                 ('0x%08x' % pc) if pc else '?'))
        if not sp:
            continue
        frames = 0
        for i in range(args.depth):
            val = tasks.u32(m, sp + 4 * i)
            if val is None or not (LOAD <= val < LOAD + len(img)):
                continue
            why = is_call_site(img, val)
            if not why:
                continue
            tag = named.get(val, '')
            print('   +%-4d 0x%08x   <- %s %s' % (4 * i, val, why, tag))
            frames += 1
            if frames >= 14:
                break
        if not frames:
            print('   (no call-site return addresses found)')


if __name__ == '__main__':
    main()
