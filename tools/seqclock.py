#!/usr/bin/env python3
"""Model the missing sequencer clock, then run a loop.

The sequencer is fine -- arming it sets the transport flag and moves the
playhead one step -- but it then starves, because its tick is driven by an
audio sample clock produced by a coprocessor this emulator does not model.
A missing clock source is not a dead end: it is a peripheral to model, which
is exactly what emu/pit.py, emu/dtim.py, emu/esdhc.py and emu/edma.py already
do for other hardware that is not there.

THE MECHANISM (measured from the image):
  The tick is INTC0 source 44, a SOFTWARE-FORCED interrupt. 0xFC048010 is
  INTFRCH (force, sources 32-63), so source 44 is bit 12:

    trigger   0x40078406  or.l  #$1000,     $fc048010
    ISR entry 0x4006e75a  and.l #$ffffefff, $fc048010   (clears its own bit)
    ISR exit  0x4006eae4  or.l  #$2000000,  $fc048010   (chains source 57)

  Vector = 64 + 44 = 108. The scheduler at 0x400783c4 subtracts the period
  (0x4020db60) from a countdown (0x80001f54) on each audio callback and forces
  the tick when it reaches zero. Nothing feeds that accumulator here, so the
  countdown never expires.

WHAT THIS DOES: raises vector 108 on a schedule, which is what the audio
callback would have done. The firmware's own ISR then advances the pattern and
emits MIDI clock -- none of that logic is faked.

CADENCE: the ISR emits exactly one MIDI clock (0xF8) per tick, and MIDI clock
is 24 PPQN. At the project's 98 BPM that is 98*24/60 = 39.2 ticks/sec, and one
16th-note step is 6 ticks. The emulator paces the guest at ~4.68M instructions
per second, so a tick is about 4_680_000/39.2 = 119_400 instructions.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panel, symbols
from emu.uiresume import open_snapshot

from unicorn.m68k_const import UC_M68K_REG_A7, UC_M68K_REG_PC, UC_M68K_REG_SR

SEQ_START = 0x4006EDF4
SENTINEL = 0x400004E8
TICK_VECTOR = 108
INTFRCH = 0xFC048010      # INTC0 force, sources 32-63; source 44 = bit 12
TICK_FORCE_BIT = 1 << 12
# The handler ENTRY, not the lea four bytes in. at() installs a
# block-begin hook, so a mid-block address never fires even while the
# code around it runs -- which is how the tick looked dead when it
# was not. Confirmed from the guest vector table: slot 108 (INTC
# source 44) holds exactly this address.
TICK_ISR = 0x4006E756
CLOCK_F8 = 0x4006E992
TRANSPORT_FLAG = 0x4199DC2C   # Digitakt mk1 OS 1.53 fallback only; rebound from
                              # prof.transport_state once the image is resolved
CUR_STEP = 0x4199DC30
TICK_IN_STEP = 0x4199DC31
INSTR_PER_SEC = 4_680_000


def u32(m, a):
    try:
        return struct.unpack('>I', m.uc.mem_read(a, 4))[0]
    except Exception:
        return None


def u16(m, a):
    try:
        return struct.unpack('>H', m.uc.mem_read(a, 2))[0]
    except Exception:
        return None


def u8(m, a):
    try:
        return m.uc.mem_read(a, 1)[0]
    except Exception:
        return None


def call_guest(m, pits, pc, fn, arg, hits):
    """Call a guest cdecl function with one long argument, then restore."""
    from unicorn.m68k_const import (UC_M68K_REG_A0, UC_M68K_REG_A1,
                                    UC_M68K_REG_A2, UC_M68K_REG_A3,
                                    UC_M68K_REG_A4, UC_M68K_REG_A5,
                                    UC_M68K_REG_A6, UC_M68K_REG_D0,
                                    UC_M68K_REG_D1, UC_M68K_REG_D2,
                                    UC_M68K_REG_D3, UC_M68K_REG_D4,
                                    UC_M68K_REG_D5, UC_M68K_REG_D6,
                                    UC_M68K_REG_D7)
    regs = [UC_M68K_REG_D0, UC_M68K_REG_D1, UC_M68K_REG_D2, UC_M68K_REG_D3,
            UC_M68K_REG_D4, UC_M68K_REG_D5, UC_M68K_REG_D6, UC_M68K_REG_D7,
            UC_M68K_REG_A0, UC_M68K_REG_A1, UC_M68K_REG_A2, UC_M68K_REG_A3,
            UC_M68K_REG_A4, UC_M68K_REG_A5, UC_M68K_REG_A6, UC_M68K_REG_A7,
            UC_M68K_REG_PC, UC_M68K_REG_SR]
    saved = {r: m.uc.reg_read(r) for r in regs}
    hits['ret'] = 0

    sp = (m.uc.reg_read(UC_M68K_REG_A7) - 0x400) & ~1
    m.uc.mem_write(sp, struct.pack('>I', SENTINEL))
    m.uc.mem_write(sp + 4, struct.pack('>I', arg & 0xFFFFFFFF))
    m.uc.reg_write(UC_M68K_REG_A7, sp)
    m.uc.reg_write(UC_M68K_REG_PC, fn)
    pc = fn
    for _ in range(6):
        pc, _e, _w = longrun.spin(m, pc, 2_000_000, pits=pits)
        if hits['ret']:
            break
    for r, v in saved.items():
        m.uc.reg_write(r, v)
    return saved[UC_M68K_REG_PC]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--bpm', type=float, default=98.0)
    ap.add_argument('--ticks', type=int, default=120,
                    help='sequencer ticks to deliver (24 per quarter note)')
    ap.add_argument('--png', default='')
    args = ap.parse_args()

    per_tick = int(INSTR_PER_SEC / (args.bpm * 24.0 / 60.0))

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    global TRANSPORT_FLAG
    TRANSPORT_FLAG = prof.transport_state or TRANSPORT_FLAG   # resolved per image
    m, ev, st, pc, inq, at, pits = open_snapshot(args.snapshot, args.syx, prof)

    hits = {'tick': 0, 'f8': 0, 'ret': 0}
    at(TICK_ISR, lambda uc, a, s, d: hits.__setitem__('tick', hits['tick'] + 1))
    at(CLOCK_F8, lambda uc, a, s, d: hits.__setitem__('f8', hits['f8'] + 1))
    at(SENTINEL, lambda uc, a, s, d: hits.__setitem__('ret', 1))

    pc, _e, _w = longrun.spin(m, pc, 4_000_000, pits=pits)
    # Read the tick vector from the guest's own table rather than deriving
    # it from the INTC source number. The table sits at 0x40000000, below
    # the image load address, and its page is mapped ON DEMAND -- so this
    # must come AFTER the guest has run, or every slot reads as unmapped
    # and the scan reports 'no vector slot' for a vector that is there.
    # VBR is 0x40000000 on this build, NOT 0 -- scanning at 4*n reads a table
    # that is not there and reports "no vector slot" for a vector that is.
    VBR = 0x40000000
    vec = None
    for n in range(0, 256):
        if u32(m, VBR + 4 * n) == TICK_ISR:
            vec = n
            break
    if vec is None:
        print('the tick ISR 0x%08x is in NO vector slot' % TICK_ISR)
    else:
        print('tick ISR 0x%08x is vector %d (slot 0x%03x) -- INTC source %d'
              % (TICK_ISR, vec, 4 * vec, vec - 64))
        globals()['TICK_VECTOR'] = vec

    print('arming the transport (seq_transport_play(0) at 0x%08x)' % SEQ_START)
    print('  slot %d before arming = 0x%08x'
          % (vec or 108, u32(m, 0x40000000 + 4 * (vec or 108)) or 0))
    pc = call_guest(m, pits, pc, SEQ_START, 0, hits)
    print('  transport=%s step=%s' % (u32(m, TRANSPORT_FLAG), u8(m, CUR_STEP)))
    slot = 0x40000000 + 4 * (vec or 108)
    print('  slot %d after arming  = 0x%08x' % (vec or 108, u32(m, slot) or 0))
    # The audio engine owns this vector and leaves the default stub in it when
    # it is not running. Put the real handler back: the emulator is standing in
    # for the audio engine here, so it installs the vector the way that engine
    # would. The ISR's own logic is untouched.
    m.uc.mem_write(slot, struct.pack('>I', TICK_ISR))
    print('  slot %d reinstalled  = 0x%08x' % (vec or 108, u32(m, slot) or 0))

    # The ISR's first gate: 0x4006e76e tst.l $41991ba4 / beq.w $4006e982.
    # Zero there makes it skip every bit of pattern work and fall straight to
    # the MIDI-clock tail -- which is exactly the "ISR runs, playhead frozen"
    # symptom. a0 for that work comes from 0x4199dc38, the pattern-storage
    # pointer (its offsets match patternStorage_v10_t's 0x1ec68 size), so the
    # gate is only safe to open when that pointer is real.
    GATE, PATPTR = 0x41991BA4, 0x4199DC38
    gate_v, pat = u32(m, GATE), u32(m, PATPTR)
    print('  ISR gate 0x41991ba4 = %s   pattern ptr 0x4199dc38 = %s'
          % (gate_v, ('0x%08x' % pat) if pat else pat))
    if not gate_v and pat and 0x40000000 <= pat < 0x50000000:
        m.uc.mem_write(GATE, struct.pack('>I', 1))
        print('  opened the gate (pattern pointer looks valid)')
    elif not gate_v:
        print('  NOT opening the gate: pattern pointer is not usable')

    print('')
    tick_vec = globals()['TICK_VECTOR']
    print('driving the tick: vector %d every %d instructions (%.1f BPM, 24 PPQN)'
          % (tick_vec, per_tick, args.bpm))
    print('')
    seen_steps, last = [], None
    for i in range(args.ticks):
        pc, _e, _w = longrun.spin(m, pc, per_tick, pits=pits)
        # Do what the audio callback's scheduler does: set the force bit, then
        # let the interrupt dispatch. The ISR clears the bit itself on entry.
        if (u32(m, slot) or 0) != TICK_ISR:
            m.uc.mem_write(slot, struct.pack('>I', TICK_ISR))
        # 0x41991ba4 is a ONE-SHOT: the ISR clears it itself at 0x4006e978, so
        # the audio engine must re-arm it for every tick it wants processed.
        # Setting it once means only the first tick does pattern work and the
        # position then freezes -- which is exactly what was observed.
        m.uc.mem_write(0x41991BA4, struct.pack('>I', 1))
        cur = u32(m, INTFRCH) or 0
        m.uc.mem_write(INTFRCH, struct.pack('>I', cur | TICK_FORCE_BIT))
        taken = m.raise_vector(tick_vec)
        # raise_vector pushes the frame and sets PC to the handler. The next
        # spin MUST start from that new PC -- passing the stale local `pc`
        # throws the interrupt entry away and the handler never runs, which
        # looks exactly like the interrupt never being taken. panelin.feed
        # returns the fresh PC for this same reason.
        if taken:
            pc = m.uc.reg_read(UC_M68K_REG_PC)
        if i < 3:
            print('    [%d] slot108=0x%08x taken=%s pc_after_raise=0x%08x'
                  % (i, u32(m, 0x40000000 + 4 * tick_vec) or 0, taken, pc),
                  flush=True)
            pc2, ex, why = longrun.spin(m, pc, 200_000, pits=pits)
            print('        after 200k: pc=0x%08x tickISR=%d f8=%d step=%s'
                  % (pc2, hits['tick'], hits['f8'], u8(m, CUR_STEP)), flush=True)
            pc = pc2
        # 0x4199dbb4 is written by the ISR every tick (0x4006e7d8) and wrapped
        # at 0x4006e7ec, so it is the sequencer's own position counter. The
        # step byte 0x4199dc30 was only a "likely" identification.
        s = u16(m, 0x4199DBB4)
        if s != last:
            seen_steps.append(s)
            print('  tick %-4d pos=%-5s step=%-3s limit=%-5s (tickISR=%d)'
                  % (i, s, u8(m, CUR_STEP), u16(m, 0x4199DD08), hits['tick']),
                  flush=True)
            last = s

    print('')
    print('== verdict ==')
    print('  tick ISR ran      : %d' % hits['tick'])
    print('  MIDI clocks (F8)  : %d' % hits['f8'])
    print('  step sequence     : %s' % seen_steps)
    distinct = len({s for s in seen_steps if s is not None})
    if distinct > 2:
        print('')
        print('  THE PLAYHEAD IS RUNNING. The pattern is advancing step by step')
        print('  under the firmware\'s own sequencer logic.')
    elif hits['tick']:
        print('  the tick ISR ran but the playhead did not advance.')
    else:
        print('  the tick ISR never ran -- vector %s did not dispatch.' % tick_vec)

    if args.png:
        buf = panel.read(m, prof.fb_front)
        if buf:
            os.makedirs(os.path.dirname(args.png) or '.', exist_ok=True)
            panel.write_png(buf, args.png, scale=6)
            print('  screen -> %s' % args.png)


if __name__ == '__main__':
    main()
