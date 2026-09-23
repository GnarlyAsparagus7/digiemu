#!/usr/bin/env python3
"""Boot Digitakt mk1 from a ladder rung to a live UI, and optionally save it.

mk1 and Digitakt II want opposite things from the intro, and only the DT2
half is recorded in emu/pit.py:

  DT2   hold every PIT. unblock force-satisfies the frame semaphore and the
        animation runs unpaced; a real PIT3 tick would post it a second time
        and the draw loop would never reach its exit test.
  mk1   deliver PIT3 and leave the frame semaphore ALONE. Measured: with
        unblock satisfying it as well, the intro exits after 77 ticks instead
        of 180 frames and the machine is dead afterwards.

emu/longrun.py already offers the second policy -- "unblock_except lists
semaphore objects to leave alone, for waits you want to drive properly
instead" -- and it also installs the handoff that stops satisfying the frame
semaphore once the intro is over, "or the draw task busy-spins at prio 7 and
starves the rest of the system". Both are guarded on profile.intro_done and
profile.frame_sem, which are None on mk1, so on mk1 neither has ever run.
The measured mk1 values are below and belong in emu/symbols.py; they are
named here as well so this tool works before that edit lands.

    frame_sem      0x41988be4   pea operand of the intro's PIT3 handler
    intro_pit3_isr 0x4006c154   vector 208 through the whole intro
    intro_done     0x4006cb94   pea frame_sem+8, hit exactly once, opens the
                                exit sequence that switches PIT3 off
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panel, symbols
from emu.dtim import Dtims, Timers
from emu.pit import Pits
from emu.snapshot import save

PIT3_VECTOR_SLOT = 0x40000340
PIT3_BASE = 0xFC08C000
# Fallbacks only. These are Digitakt mk1 OS 1.53 addresses and every one of
# them moves when the image is relinked, so they are used only if the profile
# cannot supply the symbol -- which it now can for all but POST_ISR. Keeping a
# literal here rather than reading the profile was the difference between this
# tool working on one firmware file and working on any of them.
INTRO_ISR = 0x4006C154
POST_ISR = 0x400E5CEC          # the display module's PIT3 handler; not a
                               # named symbol, derived below when possible
INTRO_DONE = 0x4006CB94
FRAME_SEM = 0x41988BE4
MAINLOOP_DEFAULT = 0x4000B6E4


def _from_profile(prof):
    """-> (intro_isr, intro_done, frame_sem, mainloop), profile first.

    profile.intro_done anchors two bytes before the `pea`, because frame_sem's
    Operand rule needs it there (see emu/symbols.py). The hook wants the pea
    itself, which is intro_done + 2.
    """
    isr = getattr(prof, 'intro_pit3_isr', None) or INTRO_ISR
    done = getattr(prof, 'intro_done', None)
    done = (done + 2) if done else INTRO_DONE
    sem = getattr(prof, 'frame_sem', None) or FRAME_SEM
    main = getattr(prof, 'mainloop', None) or MAINLOOP_DEFAULT
    return isr, done, sem, main


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
    ap.add_argument('--budget', type=lambda s: int(s, 0), default=600_000_000)
    ap.add_argument('--chunk', type=lambda s: int(s, 0), default=20_000_000)
    ap.add_argument('--png', default='out/introboot.png')
    ap.add_argument('--out', default='', help='save a snapshot here once up')
    ap.add_argument('--min-lit', type=int, default=500)
    ap.add_argument('--no-except', action='store_true',
                    help='let unblock satisfy the frame semaphore (the DT2 '
                         'policy) -- for comparison')
    args = ap.parse_args()

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    intro_isr, intro_done, frame_sem, mainloop = _from_profile(prof)
    print('symbols: intro_pit3_isr=0x%08X intro_done_hook=0x%08X '
          'frame_sem=0x%08X mainloop=0x%08X'
          % (intro_isr, intro_done, frame_sem, mainloop))

    except_sems = () if args.no_except else (frame_sem,)
    # Always the GUI's flag set: this tool exists to produce gui.snap, and
    # unblock_except is part of the checkpoint manifest, so a snapshot built
    # under one policy will not reopen under the other. tools/uisnap.py is
    # the one that builds ui.snap with default flags.
    m, ev, st, pc, inq, at = longrun.build(
        args.snapshot, syx=args.syx, unblock=True, softfloat=True,
        bitmap=True, dsp=True, unblock_except=except_sems,
        deferred_components=('timers',))

    # PIT3 paces the intro; PIT0/PIT2 and the DMA timers stay held until the
    # intro is over, because PIT2 during the intro stops the OS tasks
    # spawning at all (emu/pit.py).
    pit = Pits(m, channels=(3,), hold=False)
    dtim = Dtims(m, channels=(1, 3), hold=True)
    pits = Timers(pit, dtim)

    hits = {'mainloop': 0, 'intro_done': 0}
    at(mainloop, lambda *_: hits.__setitem__('mainloop', hits['mainloop'] + 1))

    skip = ev.get('unblock_skip')

    def handover(*_a):
        if hits['intro_done']:
            return
        hits['intro_done'] += 1
        # Stop faking the frame semaphore from here on even if it was being
        # faked, then give the OS the full timer set it expects.
        if skip is not None:
            skip.add(frame_sem)
        pit.channels = (3, 2, 0)
        pits.release()

    at(intro_done, handover)

    print('resume %s  except_sem=%s' % (os.path.basename(args.snapshot),
                                        [hex(s) for s in except_sems]))
    print('%6s %-8s %-8s %-12s %-9s %s'
          % ('instr', 'PCSR', 'lit', 'vector', 'mainloop', 'phase'))

    flipped = None
    total = 0
    while total < args.budget:
        pc, executed, _w = longrun.spin(m, pc, args.chunk, pits=pits)
        total += args.chunk
        slot = u32(m, PIT3_VECTOR_SLOT)
        buf = panel.read(m, prof.fb_front)
        lit = len(panel.lit(buf)) if buf else -1
        if flipped is None and slot not in (0, intro_isr):
            flipped = total
            print('  >> display module claimed vector 208 at %dM'
                  % (total // 1_000_000), flush=True)
        phase = ('OS' if (slot and slot != intro_isr)
                 else ('post-intro' if hits['intro_done'] else 'intro'))
        print('%5dM 0x%04x   %-8d 0x%08x   %-9d %s'
              % (total // 1_000_000, u16(m, PIT3_BASE) or 0, lit, slot or 0,
                 hits['mainloop'], phase), flush=True)
        if hits['mainloop'] > 100 and lit > args.min_lit:
            print('  -> user interface is live', flush=True)
            break

    print('')
    print('intro_done fired %d time(s); mainloop %d; lit %d'
          % (hits['intro_done'], hits['mainloop'], lit))
    buf = panel.read(m, prof.fb_front)
    if buf:
        panel.write_png(buf, args.png, scale=6)
        print('screen -> %s' % args.png)

    up = hits['mainloop'] > 100 and lit > args.min_lit
    print('VERDICT: %s' % ('USER INTERFACE IS LIVE' if up else
                           'did not reach a live UI'))

    if args.out and up:
        os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
        ev['claim_checkpoint_component']('timers', pits)
        # ev['tasks'] is a list of (entry, prio, tcb) triples; emu/tasks.py
        # wants a map keyed by TCB. dict() of the raw list raises, which is
        # why no snapshot this tool wrote has ever carried a task map.
        tasks = {'%#010x' % tcb: {'entry': entry, 'prio': prio, 'tcb': tcb}
                 for entry, prio, tcb in ev.get('tasks', [])}
        save(m, args.out, extra={'n': total, 'tasks': tasks},
             components=ev['checkpoint_components'],
             manifest=ev.get('checkpoint_manifest'))
        print('saved %s' % args.out)
    elif args.out:
        print('not saving: the UI never came up')


if __name__ == '__main__':
    main()
