#!/usr/bin/env python3
"""Turn a hard-coded address into a self-locating signature.

17 symbols in emu/symbols.py are `Fixed` -- a literal address with verify
bytes. They are what stops this being a bring-your-own-syx emulator: a
relinked build moves them, the verify bytes stop matching, and each one has to
be re-ported by hand. The six symbols that use `AnyOf(Sig(...), Fixed(...))`
already show the fix: try a masked byte-signature first, fall back to the
literal.

This generates the Sig for a given address. It takes the bytes there, wildcards
out every 4-byte window that looks like an inlined address into the image (the
thing that relocates between builds), and reports how many times the result
matches. A signature is only useful if it matches EXACTLY ONCE.

    python tools/mksig.py 0x40001b7a --len 24
    python tools/mksig.py --all          # every Fixed symbol in the table

The output is pasteable into emu/symbols.py as an `H(...)` -- the signature's
length, wildcards, a one-instruction anchor and a digest, never the firmware
bytes themselves, which stay in your own image (`--raw` prints the bytes too,
for reading locally; do not paste those). It does NOT prove the signature
survives a relink -- nothing here can, without a second firmware to test
against. It proves the signature is unique in THIS image, which is the
necessary half.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, symbols as S

LOAD = 0x40000400
CODE_LO, CODE_HI = 0x40000000, 0x40400000
DATA_LO, DATA_HI = 0x40400000, 0x48000000


def make_sig(img, addr, length, lo=CODE_LO, hi=DATA_HI):
    """-> (hex string, wildcard offsets). Absolute-looking words are masked."""
    off = addr - LOAD
    if not (0 <= off and off + length <= len(img)):
        return None, None
    raw = bytearray(img[off:off + length])
    masked = set()
    i = 0
    while i + 4 <= length:
        val = struct.unpack_from('>I', raw, i)[0]
        if lo <= val < hi:
            for k in range(i, i + 4):
                masked.add(k)
            i += 4
        else:
            i += 2
    return bytes(raw).hex(), sorted(masked)


def count_matches(img, sig_hex, masked):
    """How many places in the image match, ignoring the masked offsets."""
    raw = bytes.fromhex(sig_hex)
    n = len(raw)
    keep = [i for i in range(n) if i not in set(masked)]
    if not keep:
        return -1
    first = keep[0]
    anchor = raw[first]
    hits = 0
    for base in range(0, len(img) - n, 2):
        if img[base + first] != anchor:
            continue
        if all(img[base + i] == raw[i] for i in keep):
            hits += 1
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('addr', nargs='?', type=lambda s: int(s, 0))
    ap.add_argument('--len', type=int, default=24)
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--raw', action='store_true',
                    help='also print the literal bytes (local reading only)')
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()

    targets = []
    if args.all:
        profile = S.resolve(img)
        for name, rule, _req in S.SYMBOLS:
            if type(rule).__name__ != 'Fixed':
                continue
            val = getattr(profile, name, None)
            if isinstance(val, int):
                targets.append((name, val))
    elif args.addr is not None:
        targets.append(('(address)', args.addr))
    else:
        raise SystemExit('give an address or --all')

    print('%-20s %-12s %-7s %-8s %s'
          % ('symbol', 'address', 'masked', 'matches', 'verdict'))
    good = 0
    for name, addr in targets:
        sig, masked = make_sig(img, addr, args.len)
        if sig is None:
            print('%-20s 0x%08X   (outside the image)' % (name, addr))
            continue
        hits = count_matches(img, sig, masked)
        verdict = ('UNIQUE -- usable as a Sig' if hits == 1 else
                   'no match (bug in this tool)' if hits == 0 else
                   '%d matches -- needs more bytes or a SigWhere' % hits)
        if hits == 1:
            good += 1
        print('%-20s 0x%08X   %-7d %-8d %s'
              % (name, addr, len(masked), hits, verdict))

    if args.all:
        print('')
        print('%d of %d Fixed symbols are uniquely identifiable at %d bytes.'
              % (good, len(targets), args.len))
        print('Those can become AnyOf(Sig(...), Fixed(...)) today. The rest')
        print('need a longer window or an operand tie-break (SigWhere).')
    elif targets:
        name, addr = targets[0]
        sig, masked = make_sig(img, addr, args.len)
        print('')
        print('signature (paste into emu/symbols.py as Sig(...)\'s first argument):')
        print('    %r' % S.H.of(bytes.fromhex(sig), masked, img=img))
        if args.raw:
            print('')
            print('the bytes it stands for (local reading only, do not paste):')
            for i in range(0, len(sig), 48):
                print("    '%s'" % sig[i:i + 48])


if __name__ == '__main__':
    main()
