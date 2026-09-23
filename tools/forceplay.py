#!/usr/bin/env python3
"""Call seq_transport_play(0) directly and watch the playhead.

Pressing PLAY does not reach the transport: the key reaches
ViewController::handleKeyEvent but TransportView::consumeKeyEvent
(0x4003c9b0) switches on an INTERNAL key enum where 9/10/11 are the
RECORD-combo actions ("Live Rec", "Step Rec"), not the plain transport keys.
Finding which view owns plain PLAY is a separate thread.

The question this answers is different and more fundamental: CAN the emulated
machine run its sequencer at all? seq_transport_play (0x4006edf4) is reachable
from non-UI call sites (0x400d4954, 0x400d49e4), so it can simply be called.

It has FOUR preconditions, each bailing to the shared epilogue 0x4006f292:
   1. tst.l  0x421fb250            -- a normalizing boolean
   2. jsr    0x40076f86 ; bne      -- bails when that returns non-zero
   3. virtual via 0x4199dd58 +8, then tst.l 0x40(a7) ; beq
   4. tst.l  0x44(a7) ; beq
so "it bailed" is a real outcome and worth reporting precisely.

Calling convention: one 4-byte argument pushed by the caller, cdecl. The call
is made on a scratch frame with a sentinel return address that is hooked; when
the sentinel is reached the original register state is restored, so the guest
is left exactly as it was apart from the function's intended side effects.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panel, symbols
from emu.uiresume import open_snapshot

from unicorn.m68k_const import (UC_M68K_REG_A0, UC_M68K_REG_A1, UC_M68K_REG_A2,
                                UC_M68K_REG_A3, UC_M68K_REG_A4, UC_M68K_REG_A5,
                                UC_M68K_REG_A6, UC_M68K_REG_A7, UC_M68K_REG_D0,
                                UC_M68K_REG_D1, UC_M68K_REG_D2, UC_M68K_REG_D3,
                                UC_M68K_REG_D4, UC_M68K_REG_D5, UC_M68K_REG_D6,
                                UC_M68K_REG_D7, UC_M68K_REG_PC, UC_M68K_REG_SR)

REGS = [UC_M68K_REG_D0, UC_M68K_REG_D1, UC_M68K_REG_D2, UC_M68K_REG_D3,
        UC_M68K_REG_D4, UC_M68K_REG_D5, UC_M68K_REG_D6, UC_M68K_REG_D7,
        UC_M68K_REG_A0, UC_M68K_REG_A1, UC_M68K_REG_A2, UC_M68K_REG_A3,
        UC_M68K_REG_A4, UC_M68K_REG_A5, UC_M68K_REG_A6, UC_M68K_REG_A7,
        UC_M68K_REG_PC, UC_M68K_REG_SR]

SEQ_START = 0x4006EDF4
FLAG_STORE = 0x4006F072      # past every bail
BAIL = 0x4006F292            # the shared early-return epilogue
SENTINEL = 0x400004E8        # the image entry point: never executed post-boot
TRANSPORT_FLAG = 0x4199DC2C   # Digitakt mk1 OS 1.53 fallback only; rebound from
                              # prof.transport_state once the image is resolved
CUR_STEP = 0x4199DC30
TICK_ISR = 0x4006E75A
CLOCK_F8 = 0x4006E992


def u32(m, a):
    try:
        return struct.unpack('>I', m.uc.mem_read(a, 4))[0]
    except Exception:
        return None


def u8(m, a):
    try:
        return m.uc.mem_read(a, 1)[0]
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--arg', type=lambda s: int(s, 0), default=0)
    ap.add_argument('--slices', type=int, default=12)
    ap.add_argument('--step', type=lambda s: int(s, 0), default=6_000_000)
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    global TRANSPORT_FLAG
    TRANSPORT_FLAG = prof.transport_state or TRANSPORT_FLAG   # resolved per image
    m, ev, st, pc, inq, at, pits = open_snapshot(args.snapshot, args.syx, prof)

    hits = {'flag': 0, 'bail': 0, 'tick': 0, 'f8': 0, 'done': 0}
    at(FLAG_STORE, lambda uc, a, s, d: hits.__setitem__('flag', hits['flag'] + 1))
    at(BAIL, lambda uc, a, s, d: hits.__setitem__('bail', hits['bail'] + 1))
    at(TICK_ISR, lambda uc, a, s, d: hits.__setitem__('tick', hits['tick'] + 1))
    at(CLOCK_F8, lambda uc, a, s, d: hits.__setitem__('f8', hits['f8'] + 1))

    saved = {}

    def on_sentinel(uc, a, size, data):
        if hits['done']:
            return
        hits['done'] = 1
        for r, v in saved.items():
            uc.reg_write(r, v)

    at(SENTINEL, on_sentinel)

    # The tick is a SOFTWARE-FORCED interrupt, not a hardware timer: it is
    # raised from these two sites, and its cadence comes from a sample-clock
    # accumulator in internal SRAM. If neither site ever executes, the clock
    # that would advance the playhead simply does not exist in emulation.
    TRIGGERS = [('trigger_a', 0x40078406), ('trigger_b', 0x400D4546)]
    for name, addr in TRIGGERS:
        hits[name] = 0
        at(addr, (lambda n: (lambda uc, a, s_, d:
                             hits.__setitem__(n, hits[n] + 1)))(name))
    ACC, COUNTDOWN, PERIOD = 0x80001F4C, 0x80001F54, 0x4020DB60

    pc, _e, _w = longrun.spin(m, pc, 4_000_000, pits=pits)
    print('before: transport=%s step=%s' % (u32(m, TRANSPORT_FLAG), u8(m, CUR_STEP)))

    # Build the call frame on a scratch area below the current stack.
    for r in REGS:
        saved[r] = m.uc.reg_read(r)
    sp = (m.uc.reg_read(UC_M68K_REG_A7) - 0x400) & ~1
    m.uc.mem_write(sp, struct.pack('>I', SENTINEL))
    m.uc.mem_write(sp + 4, struct.pack('>I', args.arg & 0xFFFFFFFF))
    m.uc.reg_write(UC_M68K_REG_A7, sp)
    m.uc.reg_write(UC_M68K_REG_PC, SEQ_START)
    print('calling seq_transport_play(0x%x) at 0x%08x' % (args.arg, SEQ_START))

    pc = SEQ_START
    for _ in range(6):
        pc, _e, _w = longrun.spin(m, pc, 2_000_000, pits=pits)
        if hits['done']:
            break
    print('  returned=%s  flag_store=%d  bailed=%d'
          % (bool(hits['done']), hits['flag'], hits['bail']))
    print('  after call: transport=%s step=%s'
          % (u32(m, TRANSPORT_FLAG), u8(m, CUR_STEP)))

    print('')
    print('== watching the playhead ==')
    steps = []
    for i in range(args.slices):
        pc, _e, _w = longrun.spin(m, pc, args.step, pits=pits)
        s = u8(m, CUR_STEP)
        steps.append(s)
        print('  t%-2d transport=%s step=%s tickISR=%d midiF8=%d'
              % (i, u32(m, TRANSPORT_FLAG), s, hits['tick'], hits['f8']), flush=True)

    print('')
    print('== tick source ==')
    print('  forced-interrupt trigger sites: %s'
          % {n: hits[n] for n, _ in TRIGGERS})
    print('  sample-clock accumulator 0x80001f4c = %s' % u32(m, ACC))
    print('  tick countdown          0x80001f54 = %s' % u32(m, COUNTDOWN))
    print('  tick period             0x4020db60 = %s' % u32(m, PERIOD))

    print('')
    print('== verdict ==')
    print('  steps: %s' % steps)
    if len({s for s in steps if s is not None}) > 1:
        print('  THE PLAYHEAD IS MOVING -- the emulated sequencer is running.')
    elif hits['bail'] and not hits['flag']:
        print('  seq_transport_play BAILED on one of its four preconditions;')
        print('  the transport was never armed.')
    else:
        print('  transport flag=%s but the step did not advance.'
              % u32(m, TRANSPORT_FLAG))


if __name__ == '__main__':
    main()
