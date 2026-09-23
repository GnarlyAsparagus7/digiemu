#!/usr/bin/env python3
"""Trace the RTOS tick chain and find the link that is broken.

emu/tasks.py shows every task blocked inside sem_pend with only the prio-0
idle spin on the ready list -- a deadlock, not starvation. The tick that
should break it runs like this on this build:

    0x40002ccc  PIT2 ISR: ack PCSR, then sem_post(0x4399d7c4)
    0x40001770  the post itself
    0x40002cfa  tick_dispatch, the prio-10 software-timer wheel task
    0x40002d24  tick_pend, where that task waits for the tick
    0x4005f17e  dtim3_init, which the wheel is supposed to reach

Each is hooked and counted, so the answer is "the chain stops after N",
which names the broken link instead of guessing at it.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, symbols
from emu.uiresume import open_snapshot

LINKS = [
    ('pit2_isr', 0x40002ccc),
    ('sem_post', 0x40001770),
    ('tick_pend', 0x40002d24),
    ('ctx_switch', 0x40000410),
    # The application main task's startup, which is what actually has to run
    # for the UI to exist. It loops eight times (once per track) and only
    # then arms DTIM3 and falls into its message loop, so counting the loop
    # head against the loop exit says whether it is stuck mid-init or never
    # started at all.
    # PIT3's ISR: acks 0xFC08C000 then sem_post(0x41988be4), which wakes the
    # prio-7 display task, which releases the app task via 0x41988bec. The
    # whole UI hangs off this one interrupt.
    ('pit3_isr', 0x4006c154),
    # Is the ported queue_send address real? The DTIM3 ISR calls it
    # with main_queue on every tick, so if dtim3_isr fires and this
    # does not, the address is wrong.
    ('queue_send', 0x40001b7a),
    ('queue_recv', 0x40001c2a),
    # RESUME points, not task entries. A task entry executes once at
    # creation, long before any snapshot, so hooking it always reads 0 and
    # says nothing. What matters is whether the task comes BACK from its
    # pend, which is the instruction after the call.
    ('pit3_post_call', 0x4006c170),      # the sem_post inside PIT3's ISR
    ('display_resume', 0x4006cb5a),      # prio-7 returning from pend_b
    ('display_post', 0x4006cb94),        # prio-7 releasing the app task
    ('app_resume', 0x4006c2aa),          # prio-6 returning from its pend
    ('app_task_entry', 0x40068eb6),
    ('init_loop_head', 0x4000b4ea),
    ('init_loop_test', 0x4000b538),
    ('dtim3_init', 0x4005f17e),
    ('dtim3_isr', 0x4005f150),
    ('mainloop', 0x4000b6e4),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--instrs', type=lambda s: int(s, 0), default=60_000_000)
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)

    m, ev, st, pc, inq, at, pits = open_snapshot(
        args.snapshot, args.syx, prof)

    seen = {}
    first_arg = {}

    def watch(name):
        def hook(uc, addr, size, data):
            seen[name] = seen.get(name, 0) + 1
            # For the post, record which semaphore it was handed.
            if name == 'sem_post' and 'sem_post' not in first_arg:
                try:
                    from unicorn.m68k_const import UC_M68K_REG_A7
                    sp = uc.reg_read(UC_M68K_REG_A7)
                    import struct
                    arg = struct.unpack('>I', uc.mem_read(sp + 4, 4))[0]
                    first_arg['sem_post'] = arg
                except Exception:
                    pass
        return hook

    for name, addr in LINKS:
        at(addr, watch(name))

    # longrun.spin only SERVICES the timers when it is handed a Timers --
    # without one it runs the firmware with every interrupt source dead, so
    # nothing that depends on a tick can possibly happen and the run looks
    # like a deadlock that is really just a stopped clock. emu/gui.py builds
    # exactly this pair, so this mirrors it.

    pc, executed, why = longrun.spin(m, pc, args.instrs, pits=pits)
    print('ran %d instrs, stop=%s\n' % (executed, why))
    for name, addr in LINKS:
        n = seen.get(name, 0)
        flag = '   <-- CHAIN STOPS HERE' if n == 0 else ''
        print('  %-22s 0x%08x  %8d%s' % (name, addr, n, flag))
    if 'sem_post' in first_arg:
        print('\n  first sem_post argument: 0x%08x (expect 0x4399d7c4)'
              % first_arg['sem_post'])


if __name__ == '__main__':
    main()
