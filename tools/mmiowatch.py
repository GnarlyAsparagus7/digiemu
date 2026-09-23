#!/usr/bin/env python3
"""Is the firmware spinning on a peripheral nothing models?

The documentation pass established that DSPI0 (0xFC05C000) is the
most-referenced hardware block in the image (313 opcode-anchored references),
that the firmware explicitly enables its clock via PPMCR0 module 23, and that
NOTHING in emu/ models it. Same for SSI1 (0xFC0C8000, audio) and GPIO.

A register that is never modelled reads back as whatever the memory page
holds -- usually zero -- so a driver polling it for a ready or done bit waits
for a bit that can never appear. That looks exactly like the +Drive work
stopping dead after its erase and read sweep.

This counts reads and writes per block and, for reads, records the polling
PC. A tight poll shows up as one PC with a huge read count; genuine use shows
many PCs with modest counts. It runs the mk1 boot so the numbers cover the
whole startup, not just a resumed tail.
"""
import argparse
import collections
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
from unicorn.m68k_const import UC_M68K_REG_PC

from emu import config, longrun, symbols
from emu.dtim import Dtims, Timers
from emu.pit import Pits

INTRO_DONE = 0x4006CB94

BLOCKS = [
    (0xFC05C000, 0xFC060000, 'DSPI0'),
    (0xFC03C000, 0xFC040000, 'DSPI1'),
    (0xFC0C8000, 0xFC0CC000, 'SSI1 (audio)'),
    (0xFC0BC000, 0xFC0C0000, 'SSI0 (audio)'),
    (0xEC094000, 0xEC098000, 'GPIO'),
    (0xFC090000, 0xFC094000, 'EPORT0'),
    (0xEC074000, 0xEC078000, 'UART9 (MIDI)'),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot',
                    default='snapshots/Digitakt_OS1.53/boot400M.snap')
    ap.add_argument('--budget', type=lambda s: int(s, 0), default=200_000_000)
    ap.add_argument('--chunk', type=lambda s: int(s, 0), default=20_000_000)
    ap.add_argument('--top', type=int, default=6)
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)

    m, ev, st, pc, inq, at = longrun.build(
        args.snapshot, syx=args.syx, unblock=True, softfloat=True,
        bitmap=True, dsp=True, unblock_except=(prof.frame_sem,),
        deferred_components=('timers',))

    pit = Pits(m, channels=(3,), hold=False)
    dtim = Dtims(m, channels=(1, 3), hold=True)
    pits = Timers(pit, dtim)
    skip = ev.get('unblock_skip')
    done = {'v': 0}

    def handover(*_a):
        if done['v']:
            return
        done['v'] = 1
        if skip is not None:
            skip.add(prof.frame_sem)
        pit.channels = (3, 2, 0)
        pits.release()

    at(INTRO_DONE, handover)

    reads = collections.Counter()
    writes = collections.Counter()
    read_pcs = collections.defaultdict(collections.Counter)

    def mk(lo, hi, name):
        def on_read(uc, typ, addr, size, val, data):
            reads[name] += 1
            try:
                read_pcs[name][uc.reg_read(UC_M68K_REG_PC)] += 1
            except Exception:
                pass

        def on_write(uc, typ, addr, size, val, data):
            writes[name] += 1
        m.uc.hook_add(UC_HOOK_MEM_READ, on_read, begin=lo, end=hi - 1)
        m.uc.hook_add(UC_HOOK_MEM_WRITE, on_write, begin=lo, end=hi - 1)

    for lo, hi, name in BLOCKS:
        mk(lo, hi, name)

    total = 0
    while total < args.budget:
        pc, _e, _w = longrun.spin(m, pc, args.chunk, pits=pits)
        total += args.chunk
        print('  %4dM  %s' % (total // 1_000_000,
                              '  '.join('%s r=%d w=%d' % (n, reads[n],
                                                          writes[n])
                                        for _l, _h, n in BLOCKS
                                        if reads[n] or writes[n]) or
                              '(no accesses yet)'), flush=True)

    print('')
    print('%-16s %-10s %-10s %s' % ('block', 'reads', 'writes',
                                    'hottest read PCs'))
    for _lo, _hi, name in BLOCKS:
        hot = ', '.join('0x%08X x%d' % (p, c)
                        for p, c in read_pcs[name].most_common(args.top))
        print('%-16s %-10d %-10d %s' % (name, reads[name], writes[name], hot))
    print('')
    print('A single PC with a very large read count is a poll for a bit that')
    print('nothing sets. Many PCs with modest counts is ordinary driver use.')


if __name__ == '__main__':
    main()
