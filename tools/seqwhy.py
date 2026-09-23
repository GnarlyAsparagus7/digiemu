#!/usr/bin/env python3
"""Why doesn't PLAY start the sequencer? Two candidates, one experiment.

(a) the key never reaches the transport handler -- a modal view swallows it
(b) it reaches it and the handler bails -- 0x4006edf4 opens with
    `tst.l $421fb250` / `beq` to its own epilogue, so a null there is an
    early return before any transport flag is set

Hooks the whole chain so the answer is read, not inferred:

  0x400cae88  ViewController::handleKeyEvent  -- the key entered dispatch
  0x4003ca3c  PLAY key handler in TransportView
  0x4006edf4  sequencer START entry
  0x4006f072  the store that actually sets the transport flag (past the bail)
  0x40070218  sequencer STOP entry

and reads the gate pointer 0x421fb250 plus the transport globals directly.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panelin, symbols
from emu.dtmap import names as table_names, wire_for
from emu.uiresume import open_snapshot

CHAIN = [
    ('handleKeyEvent', 0x400CAE88),
    ('TransportView.PLAY', 0x4003CA3C),
    ('seq_start', 0x4006EDF4),
    ('flag_store', 0x4006F072),
    ('seq_stop', 0x40070218),
]
GATE = 0x421FB250
FLAGS = [('transport', 0x4199DC2C), ('mirror', 0x4199DC34),
         ('state', 0x4199DBB0), ('seq_obj', 0x421FB250),
         ('project_ptr', 0x421F9B90), ('seq_states', 0x421F9B80)]


def u32(m, a):
    try:
        return struct.unpack('>I', m.uc.mem_read(a, 4))[0]
    except Exception:
        return None


def dump(m, tag):
    print('  %-9s %s' % (tag, '  '.join(
        '%s=%s' % (n, ('0x%08x' % v) if v else v) for n, v in
        ((n, u32(m, a)) for n, a in FLAGS))), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--press', default='PLAY')
    ap.add_argument('--step', type=lambda s: int(s, 0), default=8_000_000)
    ap.add_argument('--call-start', action='store_true',
                    help='also force-call the START routine directly')
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    # FLAGS carries the mk1 literals; rebind transport (and its +8 mirror) per image
    if prof.transport_state is not None:
        FLAGS[0] = ("transport", prof.transport_state)
        FLAGS[1] = ("mirror", prof.transport_state + 8)
    names = table_names(img)
    m, ev, st, pc, inq, at, pits = open_snapshot(args.snapshot, args.syx, prof)

    hits = {n: 0 for n, _ in CHAIN}
    for name, addr in CHAIN:
        at(addr, (lambda n: (lambda uc, a, s, d: hits.__setitem__(n, hits[n] + 1)))(name))

    pc, _e, _w = longrun.spin(m, pc, args.step, pits=pits)
    print('== before ==')
    dump(m, 'idle')

    code = names[args.press.upper()]
    ch, bit = wire_for(code)
    print('')
    print('== press %s (code %d -> ch %d bit %d) ==' % (args.press, code, ch, bit))
    pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 1 << bit))
    pc, _e, _w = longrun.spin(m, pc, args.step, pits=pits)
    pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 0))
    pc, _e, _w = longrun.spin(m, pc, args.step, pits=pits)
    dump(m, 'after')

    print('')
    print('== chain ==')
    for name, addr in CHAIN:
        mark = '' if hits[name] else '   <-- NEVER REACHED'
        print('  %-20s 0x%08x  %d%s' % (name, addr, hits[name], mark))

    gate = u32(m, GATE)
    print('')
    print('gate 0x421fb250 = %s  (seq START bails to its epilogue when this is 0)'
          % (('0x%08x' % gate) if gate else gate))
    if hits['handleKeyEvent'] and not hits['TransportView.PLAY']:
        print('=> the key entered dispatch but TransportView never saw it:')
        print('   another view has first refusal (modal screen swallowing it).')
    elif hits['TransportView.PLAY'] and not hits['seq_start']:
        print('=> TransportView handled the key but did not call START.')
    elif hits['seq_start'] and not hits['flag_store']:
        print('=> START was called and BAILED before setting the flag')
        print('   -- consistent with the 0x421fb250 gate being null.')
    elif not hits['handleKeyEvent']:
        print('=> the key never reached the view dispatcher at all.')


if __name__ == '__main__':
    main()
