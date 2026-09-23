#!/usr/bin/env python3
"""Which ladder rung is actually resumable on mk1?

emu/run.py's usable_rung picks the newest rung whose intro is both live and
NOT already parked on the frame semaphore, because a parked intro cannot be
resumed at all: unblock only force-satisfies a sem_pend on the way IN, so it
can never satisfy a wait that is already blocked, and the timers that could
post the semaphore are held for as long as the intro owns vector 208. On mk1
that screen never runs -- it returns early because intro_pit3_isr and
frame_sem are None -- so the GUI always takes the 400M default sight unseen.

frame_sem is 0x41988be4: the operand of the pea in mk1's intro PIT3 handler
at 0x4006c154, which is the same relationship symbols.py's SigWhere asserts
upstream (the handler posts frame_sem; intro_done peas frame_sem+8).
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu.snapshot import restore

PIT3_VECTOR_SLOT = 0x40000340
PIT3_BASE = 0xFC08C000
INTRO_ISR = 0x4006C154
FRAME_SEM = 0x41988BE4

PREFIX = 'snapshots/Digitakt_OS1.53/boot'
RUNGS = [60, 120, 200, 280, 400]

print('%-8s %-12s %-8s %-6s %-12s %s'
      % ('rung', 'PIT3 vector', 'PCSR', 'live', 'waiter', 'usable'))
for n in RUNGS:
    path = '%s%dM.snap' % (PREFIX, n)
    if not os.path.exists(path):
        print('%-8s (missing)' % ('%dM' % n))
        continue
    try:
        m, _extra, _regs = restore(path)
        slot = struct.unpack('>I', m.uc.mem_read(PIT3_VECTOR_SLOT, 4))[0]
        pcsr = struct.unpack('>H', m.uc.mem_read(PIT3_BASE, 2))[0]
        sem = m.uc.mem_read(FRAME_SEM, 16)
        waiter = struct.unpack('>I', sem[4:8])[0]
    except Exception as exc:
        print('%-8s FAILED: %s' % ('%dM' % n, exc))
        continue
    live = slot == INTRO_ISR and bool(pcsr & 1)
    print('%-8s 0x%08x   0x%04x   %-6s 0x%08x   %s   sem=%s'
          % ('%dM' % n, slot, pcsr, live, waiter,
             'YES' if (live and not waiter) else 'no', sem.hex()))
