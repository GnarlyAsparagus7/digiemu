#!/usr/bin/env python3
"""Is the sequencer running? Watch the playhead, not the screen.

The OLED was the wrong instrument: Digitakt parameter pages carry no playhead,
so pressing PLAY and diffing frames reported nothing either way. Static
analysis of the image found the right watch points instead:

  0x4199dc2c  transport flag   -- 1 while playing, 2 after a stop
  0x4199dc30  current step     -- the playhead, one byte
  0x4199dc31  tick within step
  0x4006e75a  sequencer tick ISR -- advances the pattern, emits MIDI clock
  0x4006e992  the `pea #$f8` that emits one MIDI clock byte per tick

A running sequencer must move 0x4199dc30. A stopped one cannot. That is a
direct, unambiguous measurement that does not depend on anything being drawn.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panelin, symbols
from emu.dtmap import names as table_names, wire_for
from emu.uiresume import open_snapshot

TRANSPORT_FLAG = 0x4199DC2C   # Digitakt mk1 OS 1.53 fallback only; rebound from
                              # prof.transport_state once the image is resolved
CUR_STEP = 0x4199DC30
TICK_IN_STEP = 0x4199DC31
TICK_ISR = 0x4006E75A
CLOCK_F8 = 0x4006E992
SEQ_START = 0x4006EDF4


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


def sample(m, hits, label):
    print('  %-10s transport=%s  step=%s  tick=%s  tickISR=%d  midiF8=%d'
          % (label, u32(m, TRANSPORT_FLAG), u8(m, CUR_STEP), u8(m, TICK_IN_STEP),
             hits['tick'], hits['f8']), flush=True)
    return u8(m, CUR_STEP)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--press', default='PLAY')
    ap.add_argument('--slices', type=int, default=10)
    ap.add_argument('--step', type=lambda s: int(s, 0), default=6_000_000)
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    global TRANSPORT_FLAG
    TRANSPORT_FLAG = prof.transport_state or TRANSPORT_FLAG   # resolved per image
    names = table_names(img)
    m, ev, st, pc, inq, at, pits = open_snapshot(args.snapshot, args.syx, prof)

    hits = {'tick': 0, 'f8': 0}
    at(TICK_ISR, lambda uc, a, s, d: hits.__setitem__('tick', hits['tick'] + 1))
    at(CLOCK_F8, lambda uc, a, s, d: hits.__setitem__('f8', hits['f8'] + 1))

    print('== stopped ==')
    steps_stopped = []
    for _ in range(4):
        pc, _e, _w = longrun.spin(m, pc, args.step, pits=pits)
        steps_stopped.append(sample(m, hits, 'idle'))

    code = names[args.press.upper()]
    ch, bit = wire_for(code)
    print('')
    print('== press %s (code %d -> channel %d bit %d) ==' % (args.press, code, ch, bit))
    pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 1 << bit))
    pc, _e, _w = longrun.spin(m, pc, 3_000_000, pits=pits)
    pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 0))
    pc, _e, _w = longrun.spin(m, pc, 3_000_000, pits=pits)

    before_tick = hits['tick']
    steps_running = []
    for _ in range(args.slices):
        pc, _e, _w = longrun.spin(m, pc, args.step, pits=pits)
        steps_running.append(sample(m, hits, 'playing'))

    print('')
    print('== verdict ==')
    print('   step values while stopped : %s' % steps_stopped)
    print('   step values after press   : %s' % steps_running)
    print('   sequencer tick ISR fired  : %d time(s) after the press'
          % (hits['tick'] - before_tick))
    moved = len({s for s in steps_running if s is not None}) > 1
    flag = u32(m, TRANSPORT_FLAG)
    if moved:
        print('   THE PLAYHEAD IS MOVING -- the sequencer is running a pattern.')
    elif flag == 1:
        print('   transport flag is 1 (playing) but the step did not advance in')
        print('   this window; try more instructions per slice.')
    else:
        print('   the transport did not start (flag=%s).' % flag)


if __name__ == '__main__':
    main()
