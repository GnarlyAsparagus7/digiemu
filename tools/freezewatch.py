#!/usr/bin/env python3
"""Reproduce the samples-folder freeze and find where the main loop wedges.

The user reports the screen freezes when they press YES or NO trying to load
the samples folder. The GUI status line already showed the signature of it:
the `mainloop` counter stops advancing while timers keep ticking. So the main
UI loop has stopped, and the question is what it is stuck on.

Method: boot to the UI, run a key sequence, then probe in small chunks. Count
how many times the main loop entry is hit per chunk -- when that drops to
zero the loop is wedged. At that point sample every basic-block entry and
classify the address, so a PC stuck in the filesystem or storage code, or
parked in pend_b, is named rather than guessed.

    python freeze.py 6 12          # GLOBAL then YES
    python freeze.py 6 22 12       # GLOBAL, SAMPLE, YES
"""
import collections
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from unicorn import UC_HOOK_BLOCK                                  # noqa: E402

from emu import config, longrun, panel, panelin, symbols          # noqa: E402
from emu.dtmap import wire_for                                     # noqa: E402
from emu.uiresume import open_snapshot                            # noqa: E402

SNAP = 'snapshots/Digitakt_OS1.53/gui.snap'
SEQ = [int(a) for a in sys.argv[1:]] or [6, 12]
MAINLOOP = 0x4000B6E4
CHUNK = 5_000_000
PROBE_CHUNKS = 12

# regions, to classify a wedged PC
REGIONS = [
    (0x400d0000, 0x400d3000, 'ekFS filesystem'),
    (0x400e1000, 0x400e3000, 'eSDHC / storage driver'),
    (0x400e8800, 0x400e8a00, 'block copy'),
    (0x40001600, 0x400016c0, 'pend_b (blocked on a semaphore)'),
    (0x4000b600, 0x4000b800, 'the main UI loop itself'),
    (0x400ca000, 0x400d0000, 'UI dispatch'),
    (0x40068000, 0x4006a000, 'the mount task region'),
]


def classify(addr):
    for lo, hi, name in REGIONS:
        if lo <= addr < hi:
            return name
    return None


img = open(config.main_image(), 'rb').read()
prof = symbols.resolve(img, load_addr=0x40000400)
mainloop = getattr(prof, 'mainloop', None) or MAINLOOP
flags = dict(unblock=True, softfloat=True, bitmap=True, dsp=True)
if prof.frame_sem is not None:
    flags['unblock_except'] = (prof.frame_sem,)
m, ev, st, pc, inq, at, pits = open_snapshot(SNAP, None, prof, verbose=False,
                                             **flags)
uc = m.uc


class Held:
    def __init__(self):
        self.mask = {}

    def set(self, code, down):
        ch, bit = wire_for(code)
        v = self.mask.get(ch, 0)
        v = (v | (1 << bit)) if down else (v & ~(1 << bit))
        self.mask[ch] = v
        return panelin.encode_buttons(ch, v)


h = Held()
ml_hits = [0]
blocks = collections.Counter()
sampling = [False]


def on_block(uc_, address, size, user):
    if address == mainloop:
        ml_hits[0] += 1
    if sampling[0]:
        blocks[address] += 1


uc.hook_add(UC_HOOK_BLOCK, on_block)


def spin(n):
    global pc
    pc, ex, _w = longrun.spin(m, pc, n, pits=pits, fast=True)
    return ex


def lit():
    buf = panel.read(m, prof.fb_front)
    return len(panel.lit(buf)) if buf else None


def tap(code):
    global pc
    pc = panelin.feed(m, prof, h.set(code, True))
    spin(2_000_000)
    pc = panelin.feed(m, prof, h.set(code, False))
    spin(10_000_000)


spin(15_000_000)
print('booted, panel %s' % lit(), flush=True)
for code in SEQ:
    ml_hits[0] = 0
    tap(code)
    print('  after code %-3d panel %-6s mainloop hits during it %d'
          % (code, lit(), ml_hits[0]), flush=True)

print('\nprobing for a freeze (mainloop hits per %dM):' % (CHUNK // 1_000_000),
      flush=True)
frozen_at = None
for i in range(PROBE_CHUNKS):
    ml_hits[0] = 0
    sampling[0] = True
    blocks.clear()
    spin(CHUNK)
    sampling[0] = False
    print('  chunk %2d  mainloop hits %-6d panel %s'
          % (i, ml_hits[0], lit()), flush=True)
    if ml_hits[0] == 0 and frozen_at is None:
        frozen_at = dict(blocks)

print('\nverdict: %s'
      % ('FROZEN -- the main loop stopped being entered'
         if frozen_at is not None else
         'did not freeze in this run'), flush=True)

hist = frozen_at or dict(blocks)
top = sorted(hist.items(), key=lambda kv: -kv[1])[:20]
print('\nwhere the CPU is while frozen (top basic blocks):', flush=True)
for addr, n in top:
    tag = classify(addr) or ''
    print('  0x%08x  %8d  %s' % (addr, n, tag), flush=True)

named = collections.Counter()
for addr, n in hist.items():
    tag = classify(addr)
    if tag:
        named[tag] += n
print('\nby region:', flush=True)
for tag, n in named.most_common():
    print('  %-38s %d' % (tag, n), flush=True)
