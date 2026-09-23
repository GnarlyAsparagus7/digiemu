#!/usr/bin/env python3
"""Measure the wire (channel,bit) -> firmware control code map.

tools/panelsweep.py does this properly but insists that exactly one
queue_send caller fire per press; on this build a second, storage-related
caller (ret=0x400e1d8e) also fires and it aborts. The mapping itself does not
need that strictness -- a button record is identifiable by shape -- so this
presses every (channel,bit) in turn and reports the code byte the firmware
put in the record.

Button records seen on this build look like:

    00 00 00 00 00 00 00 CC 00 00 00 SS 00 00 00 00
                         ^^ code              ^^ state (01 down / 00 up?)

so a record is accepted as a button when byte 0 is 0 and bytes 1..6 are 0.
Everything else is ignored rather than guessed at.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panelin, symbols
from emu.uiresume import open_snapshot

from unicorn.m68k_const import UC_M68K_REG_A7


def table_name(img, index, base=0x4018e254):
    off = base - 0x40000400 + 4 * index
    if off + 4 > len(img):
        return '?'
    ptr = struct.unpack_from('>I', img, off)[0]
    o = ptr - 0x40000400
    if not (0 <= o < len(img)):
        return '?'
    end = img.find(b'\x00', o)
    return img[o:end].decode('latin1') if 0 <= end - o < 24 else '?'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--channels', default='0-5')
    ap.add_argument('--step', type=lambda s: int(s, 0), default=3_000_000)
    args = ap.parse_args()

    lo, hi = (int(x) for x in args.channels.split('-'))
    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    m, ev, st, pc, inq, at, pits = open_snapshot(args.snapshot, args.syx, prof)

    got = []

    def on_send(uc, addr, size, data):
        sp = uc.reg_read(UC_M68K_REG_A7)
        try:
            _q, item = struct.unpack('>II', uc.mem_read(sp + 4, 8))
            rec = bytes(uc.mem_read(item, 16))
        except Exception:
            return
        if rec[0] == 0 and rec[1:7] == b'\0' * 6:
            got.append(rec)

    at(prof.queue_send, on_send)

    pc, _e, _w = longrun.spin(m, pc, 4_000_000, pits=pits)
    print('%-8s %-6s %-6s %s' % ('ch/bit', 'sent', 'code', 'table[code]'))
    print('-' * 44)
    for ch in range(lo, hi + 1):
        for bit in range(8):
            got.clear()
            pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 1 << bit))
            pc, _e, _w = longrun.spin(m, pc, args.step, pits=pits)
            pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 0))
            pc, _e, _w = longrun.spin(m, pc, args.step, pits=pits)
            codes = sorted({r[7] for r in got})
            sent = ch * 8 + bit
            if codes:
                for c in codes:
                    print('%d/%-6d %-6d %-6d %s'
                          % (ch, bit, sent, c, table_name(img, c)), flush=True)
            else:
                print('%d/%-6d %-6d %-6s %s' % (ch, bit, sent, '-', '(no record)'),
                      flush=True)


if __name__ == '__main__':
    main()
