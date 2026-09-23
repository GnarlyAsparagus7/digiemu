#!/usr/bin/env python3
"""Find a key sequence that lets PLAY reach TransportView.

Established: the press reaches ViewController::handleKeyEvent (0x400cae88)
but never TransportView's PLAY handler (0x4003ca3c), so a view above it has
first refusal. The sequencer itself is ready -- gate 0x421fb250 is 1 and a
live Project exists -- so this is purely a routing problem.

Tries each candidate prefix from a FRESH resume (presses are stateful) and
reports how far down the chain PLAY got. Fresh resume per attempt matters:
attempt N in a machine that has already taken N-1 presses is measuring a
different UI state than the one named.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panel, panelin, symbols
from emu.dtmap import names as table_names, wire_for
from emu.uiresume import open_snapshot

CHAIN = [('dispatch', 0x400CAE88), ('transportView', 0x4003CA3C),
         ('seq_start', 0x4006EDF4), ('flag_store', 0x4006F072)]
TRANSPORT_FLAG = 0x4199DC2C   # Digitakt mk1 OS 1.53 fallback only; rebound from
                              # prof.transport_state once the image is resolved
CUR_STEP = 0x4199DC30
TICK_ISR = 0x4006E75A

PREFIXES = [
    [],
    ['NO'],
    ['YES'],
    ['TRIG'],
    ['PATTERN MENU'],
    ['STOP'],
    ['FUNC'],
]


def u32(m, a):
    try:
        return struct.unpack('>I', m.uc.mem_read(a, 4))[0]
    except Exception:
        return None


def tap(m, prof, pits, pc, code, step):
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
    ap.add_argument('--step', type=lambda s: int(s, 0), default=5_000_000)
    ap.add_argument('--settle', type=lambda s: int(s, 0), default=5_000_000)
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    global TRANSPORT_FLAG
    TRANSPORT_FLAG = prof.transport_state or TRANSPORT_FLAG   # resolved per image
    names = table_names(img)

    print('%-16s %-9s %-14s %-10s %-11s %s'
          % ('prefix', 'dispatch', 'transportView', 'seq_start', 'flagStore', 'flag/step/tick'))
    print('-' * 86)
    for prefix in PREFIXES:
        m, ev, st, pc, inq, at, pits = open_snapshot(
            args.snapshot, args.syx, prof, verbose=False)
        hits = {n: 0 for n, _ in CHAIN}
        hits['tick'] = 0
        for name, addr in CHAIN:
            at(addr, (lambda n: (lambda uc, a, s, d:
                                 hits.__setitem__(n, hits[n] + 1)))(name))
        at(TICK_ISR, lambda uc, a, s, d: hits.__setitem__('tick', hits['tick'] + 1))

        pc, _e, _w = longrun.spin(m, pc, args.settle, pits=pits)
        for spec in prefix:
            pc = tap(m, prof, pits, pc, names[spec.upper()], args.step)
        base = dict(hits)
        pc = tap(m, prof, pits, pc, names['PLAY'], args.step)
        pc, _e, _w = longrun.spin(m, pc, args.step * 3, pits=pits)

        flag = u32(m, TRANSPORT_FLAG)
        try:
            step = m.uc.mem_read(CUR_STEP, 1)[0]
        except Exception:
            step = None
        label = '+'.join(prefix) if prefix else '(none)'
        print('%-16s %-9d %-14d %-10d %-11d %s/%s/%d'
              % (label,
                 hits['dispatch'] - base['dispatch'],
                 hits['transportView'] - base['transportView'],
                 hits['seq_start'] - base['seq_start'],
                 hits['flag_store'] - base['flag_store'],
                 flag, step, hits['tick'] - base['tick']), flush=True)


if __name__ == '__main__':
    main()
