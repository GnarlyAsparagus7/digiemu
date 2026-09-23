#!/usr/bin/env python3
"""Press the REAL play key and see whether the sequencer runs.

Every earlier transport test pressed the wrong key. The panel-test label
table calls code 25 "PLAY", but the runtime treats 24-39 as trigs 1-16, so
code 25 is trig 2 -- which is why it selected track 2 and why TransportView
looked unreachable. Measured by state change instead of by label:

    code 10 (channel 4, bit 4) = PLAY    (sets the transport flag, calls
                                          seq_transport_play, stores the flag)
    code 11 (channel 4, bit 5) = STOP

This presses code 10 through the ordinary panel path -- no forced call, no
poked vector, no gate fiddling -- and then watches whether the sequencer
actually advances, and whether the tick vector stays installed when the
transport is armed the way the firmware intends.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panel, panelin, symbols
from emu.dtmap import wire_for
from emu.uiresume import open_snapshot

PLAY, STOP = 10, 11
VBR = 0x40000000
TICK_VEC = 108
TICK_ISR = 0x4006E756
CLOCK_F8 = 0x4006E992
TRANSPORT_FLAG = 0x4199DC2C   # Digitakt mk1 OS 1.53 fallback only; rebound from
                              # prof.transport_state once the image is resolved
POS_SRC = 0x4199DD08
POS_NEXT = 0x4199DBB4
CUR_STEP = 0x4199DC30
GATE = 0x41991BA4


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


def tap(m, prof, pits, pc, code, step):
    ch, bit = wire_for(code)
    pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 1 << bit))
    pc, _e, _w = longrun.spin(m, pc, step, pits=pits)
    pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 0))
    pc, _e, _w = longrun.spin(m, pc, step, pits=pits)
    return pc


def snap(m, hits, tag):
    print('  %-9s flag=%-3s vec108=0x%08x gate=%-3s posSrc=%-5s posNext=%-5s '
          'step=%-3s tickISR=%d f8=%d'
          % (tag, u32(m, TRANSPORT_FLAG), u32(m, VBR + 4 * TICK_VEC) or 0,
             u32(m, GATE), u16(m, POS_SRC), u16(m, POS_NEXT), u8(m, CUR_STEP),
             hits['tick'], hits['f8']), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--slices', type=int, default=10)
    ap.add_argument('--step', type=lambda s: int(s, 0), default=8_000_000)
    ap.add_argument('--png', default='')
    ap.add_argument('--drive-tick', action='store_true',
                    help='synthesise the audio sample clock: raise the tick '
                         'vector at 24 PPQN and re-arm the one-shot gate')
    ap.add_argument('--bpm', type=float, default=98.0)
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    global TRANSPORT_FLAG
    TRANSPORT_FLAG = prof.transport_state or TRANSPORT_FLAG   # resolved per image
    m, ev, st, pc, inq, at, pits = open_snapshot(args.snapshot, args.syx, prof)

    hits = {'tick': 0, 'f8': 0}
    at(TICK_ISR, lambda uc, a, s, d: hits.__setitem__('tick', hits['tick'] + 1))
    at(CLOCK_F8, lambda uc, a, s, d: hits.__setitem__('f8', hits['f8'] + 1))

    pc, _e, _w = longrun.spin(m, pc, 5_000_000, pits=pits)
    print('== before ==')
    snap(m, hits, 'idle')

    print('')
    print('== pressing PLAY (code %d -> channel %d bit %d) =='
          % (PLAY, *wire_for(PLAY)))
    pc = tap(m, prof, pits, pc, PLAY, 5_000_000)
    snap(m, hits, 'pressed')

    print('')
    print('== running ==')
    # With the transport armed through the proper key path the tick vector
    # stays installed, so a synthesised clock now has somewhere to land. The
    # gate at 0x41991ba4 is a one-shot the ISR clears itself, so it is
    # re-armed per tick exactly as the audio callback would.
    per_tick = int(4_680_000 / (args.bpm * 24.0 / 60.0))
    step = per_tick if args.drive_tick else args.step
    seen = []
    for i in range(args.slices):
        pc, _e, _w = longrun.spin(m, pc, step, pits=pits)
        if args.drive_tick:
            m.uc.mem_write(GATE, struct.pack('>I', 1))
            if m.raise_vector(TICK_VEC):
                from unicorn.m68k_const import UC_M68K_REG_PC
                pc = m.uc.reg_read(UC_M68K_REG_PC)
        seen.append((u16(m, POS_SRC), u16(m, POS_NEXT), u8(m, CUR_STEP)))
        snap(m, hits, 't%d' % i)

    print('')
    print('== verdict ==')
    src = {s[0] for s in seen}
    nxt = {s[1] for s in seen}
    stp = {s[2] for s in seen}
    print('  distinct posSrc=%d  posNext=%d  step=%d  tickISR=%d'
          % (len(src), len(nxt), len(stp), hits['tick']))
    if len(src) > 1 or len(stp) > 1:
        print('  THE SEQUENCER IS RUNNING -- the position advances on its own.')
    elif hits['tick']:
        print('  the tick fires but the position is static.')
    else:
        print('  no sequencer tick: the clock still is not being produced.')

    if args.png:
        buf = panel.read(m, prof.fb_front)
        if buf:
            os.makedirs(os.path.dirname(args.png) or '.', exist_ok=True)
            panel.write_png(buf, args.png, scale=6)
            print('  screen -> %s' % args.png)


if __name__ == '__main__':
    main()
