#!/usr/bin/env python3
"""Disassemble a span of the loaded MAIN OS image.

Everything in this port so far has been read as hex by hand, which is fine for
confirming a known signature and bad for understanding an unfamiliar routine.
Capstone has m68k support and the ColdFire V4 encodings the firmware uses are
a subset, so this just points it at the image.

    python tools/m68dis.py 0x400e5380 --count 60
    python tools/m68dis.py 0x400e5380 --until 0x400e55e0 --marks 0x400e5398,0x400e55c2

Addresses are absolute in the loaded image (load address 0x40000400), which is
what every other tool here prints, so they can be pasted straight across.
Annotates branch targets and absolute operands that fall inside the image.
"""
import argparse
import sys

from capstone import CS_ARCH_M68K, CS_MODE_BIG_ENDIAN, CS_MODE_M68K_040, Cs

sys.path.insert(0, __import__('os').path.dirname(
    __import__('os').path.dirname(__import__('os').path.abspath(__file__))))

from emu import config

LOAD = 0x40000400


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('addr', type=lambda s: int(s, 0))
    ap.add_argument('--count', type=int, default=40)
    ap.add_argument('--until', type=lambda s: int(s, 0), default=0)
    ap.add_argument('--marks', default='',
                    help='comma-separated addresses to flag in the output')
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    marks = {int(x, 0) for x in args.marks.split(',') if x.strip()}

    start = args.addr - LOAD
    if not (0 <= start < len(img)):
        raise SystemExit('0x%08x is outside the image' % args.addr)
    span = (args.until - args.addr) if args.until else (args.count * 10)
    blob = img[start:start + max(span, 16)]

    md = Cs(CS_ARCH_M68K, CS_MODE_BIG_ENDIAN | CS_MODE_M68K_040)
    md.detail = False
    shown = 0
    for ins in md.disasm(blob, args.addr):
        if args.until and ins.address >= args.until:
            break
        if not args.until and shown >= args.count:
            break
        note = ''
        # A string operand is far more useful printed than as a number.
        for tok in ins.op_str.replace('(', ' ').replace(')', ' ').split():
            tok = tok.strip('#$,.l w')
            try:
                val = int(tok, 16) if tok else 0
            except ValueError:
                continue
            off = val - LOAD
            if 0 <= off < len(img) and val > LOAD:
                end = img.find(b'\x00', off)
                if 0 < end - off < 40:
                    txt = img[off:end]
                    if all(32 <= c < 127 or c < 16 for c in txt) and \
                            sum(1 for c in txt if 32 <= c < 127) >= 4:
                        note = '  ; %r' % txt.decode('latin1')
        flag = ' <<<' if ins.address in marks else ''
        print('0x%08x  %-22s %-34s%s%s'
              % (ins.address, ins.bytes.hex(), '%s %s' % (ins.mnemonic,
                                                          ins.op_str),
                 note, flag))
        shown += 1


if __name__ == '__main__':
    main()
