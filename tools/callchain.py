#!/usr/bin/env python3
"""Walk the static call chain upward from an address.

Written because the job-worker pool is never created and the question is what
was supposed to call it. Each step finds the enclosing function (by scanning
back to the previous `rts`) and then every `jsr (abs).l` that targets it, so a
chain with a single caller at each level reads as a straight line up to
whatever decides not to run it.

    python tools/callchain.py 0x4008E0B0 --depth 8
"""
import argparse
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config

LOAD = 0x40000400


def func_start(img, addr, limit=2000):
    """-> the address just past the previous rts, i.e. the function head."""
    o = addr - LOAD
    for back in range(2, limit, 2):
        if o - back < 0:
            break
        if img[o - back:o - back + 2] == b'\x4e\x75':
            return LOAD + (o - back) + 2
    return None


def callers(img, target):
    """-> [(site, kind)] for every absolute reference to `target`."""
    out = []
    pat = struct.pack('>I', target)
    for mo in re.finditer(re.escape(pat), img):
        p = mo.start()
        if p < 2 or (p & 1):
            continue
        prev = struct.unpack_from('>H', img, p - 2)[0]
        kind = {0x4EB9: 'jsr', 0x4EF9: 'jmp', 0x4879: 'pea',
                0x203C: 'move.l #', 0x2F3C: 'push', 0x41F9: 'lea'}.get(prev)
        if kind:
            out.append((LOAD + p - 2, kind))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('addr', type=lambda s: int(s, 0))
    ap.add_argument('--depth', type=int, default=8)
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    cur = args.addr
    seen = set()
    for level in range(args.depth):
        fn = func_start(img, cur)
        if fn is None or fn in seen:
            print('%s(stopped: no enclosing function found, or a cycle)'
                  % ('  ' * level))
            break
        seen.add(fn)
        cs = callers(img, fn)
        jsrs = [c for c in cs if c[1] in ('jsr', 'jmp')]
        others = [c for c in cs if c[1] not in ('jsr', 'jmp')]
        print('%slevel %d: 0x%08X is inside function 0x%08X'
              % ('  ' * level, level, cur, fn))
        print('%s  callers: %d jsr/jmp, %d other reference(s)'
              % ('  ' * level, len(jsrs), len(others)))
        for a, k in (jsrs + others)[:6]:
            print('%s    0x%08X  %s' % ('  ' * level, a, k))
        if not jsrs:
            if others:
                print('%s  -> no direct call. Referenced only as data '
                      '(a vtable slot or a task entry pushed to '
                      'task_create).' % ('  ' * level))
            else:
                print('%s  -> NOTHING references this function at all.'
                      % ('  ' * level))
            break
        if len(jsrs) > 1:
            print('%s  -> branches; stopping the straight-line walk here.'
                  % ('  ' * level))
            break
        cur = jsrs[0][0]


if __name__ == '__main__':
    main()
