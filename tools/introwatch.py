#!/usr/bin/env python3
"""Drive mk1's intro to its handover with the timers held, and pin intro_done.

emu/pit.py states, from measurement, "Do not deliver anything while the intro
is still running": the intro advances by unblock force-satisfying its frame
semaphore, and a real PIT3 tick posts that same semaphore a second time, so
the draw loop never reaches its exit test. On mk1 intro_pit3_isr does not
resolve, so intro_running answers False, so emu/gui.py builds its timers with
hold=False and delivers straight into the intro. A cold GUI launch then sits
at PIT3 frame 20 with tasks 0 and mainloop 0 forever -- measured to 1.3e9
instructions.

This holds the timers the way gui.py would if the symbol resolved, and
watches the PIT3 vector slot. The instant it stops pointing at the intro's
handler (0x4006c154, measured from the ladder rungs) and the display
module's (0x400e5cec, measured from ui.snap) takes over, the intro has handed
over. Candidate addresses around the exit sequence are hooked so the one that
fires at that moment can be named intro_done -- and, because at() hooks are
block-begin, so the one that actually FIRES is used rather than the one that
merely looks right in a disassembly.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panel, symbols
from emu.dtim import Dtims, Timers
from emu.pit import Pits

PIT3_VECTOR_SLOT = 0x40000340
PIT3_BASE = 0xFC08C000
INTRO_ISR = 0x4006C154
POST_ISR = 0x400E5CEC

# The exit sequence, as laid out by the byte scan. Which of these is a basic
# block start is not knowable from the bytes alone, so hook them all and let
# the run say. 0x4006c154 is the intro's own PIT3 handler: it must NOT fire
# while the timers are held, and that is a control on the experiment.
CANDIDATES = [0x4006CB8E, 0x4006CB90, 0x4006CB92, 0x4006CB94, 0x4006CB9A,
              0x4006CB9E, 0x4006CBA4, 0x4006CBB0, 0x4006C154, 0x4006C29E]


def u32(m, a):
    try:
        return struct.unpack('>I', m.uc.mem_read(a, 4))[0]
    except Exception:
        return None


def u16(m, a):
    try:
        return struct.unpack('>H', m.uc.mem_read(a, 2))[0]
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--chunk', type=lambda s: int(s, 0), default=10_000_000)
    ap.add_argument('--limit', type=lambda s: int(s, 0), default=900_000_000)
    ap.add_argument('--after', type=lambda s: int(s, 0), default=150_000_000,
                    help='instructions to keep running after the handover')
    ap.add_argument('--png', default='out/introwatch.png')
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)

    # Exactly emu/gui.py's flags, so what happens here is what happens there.
    m, ev, st, pc, inq, at = longrun.build(
        args.snapshot, syx=args.syx, unblock=True, softfloat=True,
        bitmap=True, dsp=True)

    pits = Timers(Pits(m, hold=True), Dtims(m, channels=(3,), hold=True))
    claim = ev.get('claim_checkpoint_component')
    if claim:
        try:
            claim('timers', pits)
        except Exception as exc:
            print('note: could not claim timers component (%s)' % exc)

    first = {}
    count = {}
    for a in CANDIDATES:
        def mk(addr):
            def fn(uc, ad, s_, d):
                count[addr] = count.get(addr, 0) + 1
                first.setdefault(addr, st['instrs'] if 'instrs' in st else -1)
            return fn
        at(a, mk(a))

    print('resumed %s  PIT3 slot=0x%08x  PCSR=0x%04x  held=%s'
          % (os.path.basename(args.snapshot), u32(m, PIT3_VECTOR_SLOT),
             u16(m, PIT3_BASE), pits.held), flush=True)

    done_at = None
    total = 0
    while total < args.limit:
        pc, _e, _w = longrun.spin(m, pc, args.chunk, pits=pits)
        total += args.chunk
        slot = u32(m, PIT3_VECTOR_SLOT)
        pcsr = u16(m, PIT3_BASE)
        buf = panel.read(m, prof.fb_front)
        lit = len(panel.lit(buf)) if buf else -1
        if done_at is None and slot != INTRO_ISR:
            done_at = total
            pits.release()
            print('>> HANDOVER at %dM: slot 0x%08x -> 0x%08x, PCSR 0x%04x, '
                  'timers released' % (total // 1_000_000, INTRO_ISR, slot,
                                       pcsr), flush=True)
        print('  %4dM  slot=0x%08x PCSR=0x%04x lit=%-6d held=%s'
              % (total // 1_000_000, slot, pcsr, lit, pits.held), flush=True)
        if done_at is not None and total - done_at >= args.after:
            break

    print('')
    print('== candidate hooks (first hit, count) ==')
    for a in CANDIDATES:
        tag = ''
        if a == INTRO_ISR:
            tag = '  <- intro PIT3 ISR; MUST be 0 while held'
        print('  0x%08x  hits=%-6d %s' % (a, count.get(a, 0), tag))

    print('')
    if done_at is None:
        print('VERDICT: the intro did NOT hand over within %dM instructions.'
              % (args.limit // 1_000_000))
    else:
        print('VERDICT: intro handed over at ~%dM with the timers held.'
              % (done_at // 1_000_000))

    buf = panel.read(m, prof.fb_front)
    if buf:
        panel.write_png(buf, args.png, scale=6)
        print('screen (%d lit) -> %s' % (len(panel.lit(buf)), args.png))


if __name__ == '__main__':
    main()
