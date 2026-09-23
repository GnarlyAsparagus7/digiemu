#!/usr/bin/env python3
"""Prove the sequencer is running by watching the panel LEDs.

The OLED is the wrong instrument for this. A Digitakt's parameter pages carry
no playhead, so "press PLAY and diff the screen" reports nothing whether the
transport is running or not -- which is exactly what it did.

The trig LEDs are the playhead, and they are not on the OLED at all: they
live on the panel MCU, driven over UART8 TX, which emu/edma.py's channel-35
model lands in ev['uart_out']. A stopped sequencer sends almost nothing; a
running one has to send an LED update every step. So the TX byte RATE, and
the periodicity of what it sends, is the measurement.

At 98 BPM and 16th steps that is about 6.5 steps/sec of emulated time.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panelin, symbols
from emu.dtmap import names as table_names, wire_for
from emu.uiresume import open_snapshot


def tx(ev):
    return bytes(bytearray(ev.get('uart_out', b'')))


def press(m, prof, pits, pc, code, step):
    ch, bit = wire_for(code)
    pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 1 << bit))
    pc, _e, _w = longrun.spin(m, pc, step, pits=pits)
    pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 0))
    pc, _e, _w = longrun.spin(m, pc, step, pits=pits)
    return pc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    # NOT default=['PLAY']: argparse APPENDS to a list default, so
    # `--press PLAY` would press it twice -- start, then stop.
    ap.add_argument('--press', action='append')
    ap.add_argument('--window', type=lambda s: int(s, 0), default=40_000_000)
    ap.add_argument('--step', type=lambda s: int(s, 0), default=4_000_000)
    args = ap.parse_args()
    if not args.press:
        args.press = ['PLAY']

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    names = table_names(img)
    m, ev, st, pc, inq, at, pits = open_snapshot(args.snapshot, args.syx, prof)

    pc, _e, _w = longrun.spin(m, pc, 4_000_000, pits=pits)

    before = len(tx(ev))
    pc, _e, _w = longrun.spin(m, pc, args.window, pits=pits)
    stopped_bytes = len(tx(ev)) - before
    print('== stopped ==')
    print('   UART8 TX over %dM instrs: %d byte(s)'
          % (args.window // 1_000_000, stopped_bytes), flush=True)

    for spec in args.press:
        code = names.get(spec.upper())
        if code is None:
            raise SystemExit('unknown control %r' % spec)
        ch, bit = wire_for(code)
        print('\n== press %s (code %d -> channel %d bit %d) =='
              % (spec, code, ch, bit), flush=True)
        pc = press(m, prof, pits, pc, code, args.step)

    mark = len(tx(ev))
    counts = []
    for _ in range(8):
        n0 = len(tx(ev))
        pc, _e, _w = longrun.spin(m, pc, args.window // 8, pits=pits)
        counts.append(len(tx(ev)) - n0)
    running_bytes = len(tx(ev)) - mark
    print('\n== after the press ==')
    print('   UART8 TX over %dM instrs: %d byte(s)'
          % (args.window // 1_000_000, running_bytes), flush=True)
    print('   per slice: %s' % counts, flush=True)

    print('\n== verdict ==')
    print('   stopped %d bytes  vs  running %d bytes' % (stopped_bytes,
                                                         running_bytes))
    if running_bytes > max(8, stopped_bytes * 3):
        print('   TRANSPORT IS RUNNING: the panel is being driven steadily,')
        print('   which is the trig LEDs following the playhead.')
    else:
        print('   no sustained panel traffic -- the transport did not start.')

    data = tx(ev)[mark:]
    if data:
        print('\n   first 64 TX bytes after the press:')
        for i in range(0, min(64, len(data)), 16):
            print('     %s' % ' '.join('%02x' % b for b in data[i:i + 16]))


if __name__ == '__main__':
    main()
