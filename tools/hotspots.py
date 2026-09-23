#!/usr/bin/env python3
"""Where is the emulator spending its time? Histogram basic-block entries.

The GUI's throughput falls about 3x at a reproducible point: PIT3 stops
ticking at exactly 312 and the rate goes from ~9M instructions/sec to ~3.5M in
the same chunk, in every run. A rate change of that size is not the firmware
doing more work -- the guest's own clock did not change -- it is the HOST
doing more work per guest instruction, which means either many more Python
MMIO callbacks or a tight loop whose blocks are re-entered constantly.

This tells those apart by sampling where the guest actually is. --skip runs
without instrumentation so the profile can be taken in a chosen phase, then a
block hook counts entries per address over --window instructions. Comparing an
early window with a late one says what changed.

Block entries, not instructions: a per-instruction hook would itself dominate
what it is trying to measure. A tight spin shows up as a huge count on very
few addresses either way.
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unicorn import UC_HOOK_BLOCK

from emu import config, longrun, symbols
from emu.uiresume import open_snapshot

GUI_FLAGS = dict(unblock=True, softfloat=True, bitmap=True, dsp=True)

# Regions worth naming in the output, so a hot address does not have to be
# looked up by hand. Ends are exclusive and approximate -- this is a label,
# not a claim about function boundaries.
REGIONS = [
    (0x40000000, 0x40003000, 'RTOS core (sem/task/tick)'),
    (0x4005f000, 0x40060000, 'DTIM3'),
    (0x4006c000, 0x4006d000, 'boot intro module'),
    (0x4008e000, 0x4008f000, 'job worker'),
    (0x400cf000, 0x400d0000, 'coprocessor transport'),
    (0x400e0000, 0x400e3000, 'storage driver (eSDHC)'),
    (0x400e5000, 0x400e6000, 'display module'),
    (0x40120000, 0x40130000, 'softfloat / misc runtime'),
    (0x8C000000, 0x8C010000, 'COPROCESSOR PORT'),
]


def region_of(addr):
    for lo, hi, name in REGIONS:
        if lo <= addr < hi:
            return name
    return ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', default='snapshots/Digitakt_OS1.53/gui.snap')
    ap.add_argument('--skip', type=lambda s: int(s, 0), default=0)
    ap.add_argument('--window', type=lambda s: int(s, 0), default=20_000_000)
    ap.add_argument('--top', type=int, default=18)
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    flags = dict(GUI_FLAGS)
    if prof.frame_sem is not None:
        flags['unblock_except'] = (prof.frame_sem,)
    m, ev, st, pc, inq, at, pits = open_snapshot(
        args.snapshot, args.syx, prof, **flags)

    # Name a few resolved symbols so hot addresses read as something.
    named = {}
    for name in ('sem_pend', 'pend_b', 'sem_post', 'ctx_switch', 'mainloop',
                 'job_pump', 'panel_diff', 'display_frame_post', 'sd_bringup',
                 'tick_dispatch'):
        addr = getattr(prof, name, None)
        if isinstance(addr, int):
            named[addr] = name

    if args.skip:
        print('skipping %dM instructions (uninstrumented)...'
              % (args.skip // 1_000_000), flush=True)
        done = 0
        while done < args.skip:
            pc, executed, _w = longrun.spin(m, pc, 20_000_000, pits=pits)
            done += 20_000_000
            print('  %dM' % (done // 1_000_000), flush=True)

    counts = collections.Counter()

    def on_block(uc, address, size, data):
        counts[address] += 1

    handle = m.uc.hook_add(UC_HOOK_BLOCK, on_block)

    print('profiling %dM instructions...' % (args.window // 1_000_000),
          flush=True)
    pc, executed, _w = longrun.spin(m, pc, args.window, pits=pits)
    m.uc.hook_del(handle)

    total = sum(counts.values())
    print('')
    print('%d block entries over %d instructions (%.1f instrs per block)'
          % (total, executed, executed / max(total, 1)))
    print('')
    print('%-12s %-10s %-7s %-26s %s'
          % ('address', 'entries', 'share', 'region', 'symbol'))
    for addr, n in counts.most_common(args.top):
        print('0x%08x   %-10d %5.1f%%  %-26s %s'
              % (addr, n, 100.0 * n / max(total, 1), region_of(addr),
                 named.get(addr, '')))


if __name__ == '__main__':
    main()
