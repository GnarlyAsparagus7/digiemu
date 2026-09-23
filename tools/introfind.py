#!/usr/bin/env python3
"""Locate mk1's intro PIT3 handler and its exit sequence.

emu/symbols.py resolves 'intro_pit3_isr' and 'intro_done' by signature, and
neither signature matches the Digitakt mk1 image -- both come back None. That
is not cosmetic. emu/pit.py's intro_running documents that it returns False
when the ISR did not resolve "since there is then no way to tell", so on mk1
every caller believes the intro is not running. emu/gui.py:420 then builds
its timers with hold=False and delivers PIT0/PIT2/PIT3 into the intro as it
starts, which stops the intro ever handing over: a cold GUI launch sits at
PIT3 frame 20 forever (measured out to 1.3e9 instructions).

The ISR is identified by measurement rather than by signature: during the
intro the PIT3 vector slot holds the intro's handler, and the intro switches
PIT3 off on its way out, so the same slot in a post-intro snapshot holds the
display module's handler instead. Whichever address the slot holds while
PIT3 is still enabled IS the intro handler, by the definition intro_running
itself uses.
"""
import argparse
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun

PIT3_VECTOR_SLOT = 0x40000340
PIT3_BASE = 0xFC08C000
LOAD = 0x40000400


def probe(snapshot, syx):
    m, ev, st, pc, inq, at = longrun.build(
        snapshot, syx=syx, deferred_components=('timers',))
    slot = struct.unpack('>I', m.uc.mem_read(PIT3_VECTOR_SLOT, 4))[0]
    pcsr = struct.unpack('>H', m.uc.mem_read(PIT3_BASE, 2))[0]
    return slot, pcsr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshots', nargs='+', default=[])
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()

    print('== PIT3 vector slot (0x%08x) per snapshot ==' % PIT3_VECTOR_SLOT)
    print('%-16s %-12s %-8s %s' % ('snapshot', 'handler', 'PCSR', 'enabled'))
    for snap in args.snapshots:
        name = os.path.basename(snap)
        try:
            slot, pcsr = probe(snap, args.syx)
        except Exception as exc:
            print('%-16s FAILED: %s' % (name, exc), flush=True)
            continue
        print('%-16s 0x%08x   0x%04x   %s'
              % (name, slot, pcsr, 'yes' if (pcsr & 1) else 'no'), flush=True)

    print('')
    print('== candidate ISR prologues (lea -16,a7 / movem.l d0-d1,a0-a1) ==')
    for mo in re.finditer(re.escape(b'\x4f\xef\xff\xf0\x48\xd7\x03\x03'), img):
        print('  0x%08x  %s'
              % (LOAD + mo.start(), img[mo.start():mo.start() + 28].hex()))

    print('')
    print('== sites that switch PIT3 off (move.w Dn,(0xfc08c000)) ==')
    for mo in re.finditer(re.escape(b'\xfc\x08\xc0\x00'), img):
        o = mo.start()
        op = img[o - 2:o]
        if op[:1] != b'\x33':
            continue
        print('  0x%08x  op=%s  context=%s'
              % (LOAD + o - 2, op.hex(), img[max(0, o - 24):o + 16].hex()))


if __name__ == '__main__':
    main()
