#!/usr/bin/env python3
"""Deterministic post-boot milestone check for Digitakt mk1: from a ladder
rung, run a fixed instruction budget and assert the things someone staring at
the GUI would call "it worked".

This asks a different question from tools/bootcheck.py. bootcheck answers
"did the OS take over" (MAIN_OS_RUNNING). This answers "is this run
trustworthy", which is not the same thing and can disagree: a frame that only
looks like progress, a +Drive the firmware quietly refused to mount, and a
pend list showing `unblock` raced ahead of a real poster are each a false
MAIN_OS_RUNNING. Every one of those has actually happened in this port.

    python tools/emucheck.py --snapshot snapshots/Digitakt_OS1.53/gui.snap
    python tools/emucheck.py --snapshot snapshots/Digitakt_OS1.53/boot400M.snap \\
        --syx Digitakt_OS1.53.syx --instrs 300000000

Exit status is 0 only if every check passes, so it can gate a rebuild.

Adapted from upstream digikit's tools/emucheck.py. Three things had to
change for mk1, all of them because mk1 is not Digitakt II:

  * Timers come from emu/uiresume.py instead of being built here. Upstream
    builds its own and holds them until intro_done. mk1 leaves the intro
    frame semaphore alone (with unblock satisfying it the intro exits after
    77 ticks instead of 180 frames and the machine is dead afterwards, see
    tools/introboot.py), and a snapshot taken mid-run has to be resumed with
    its own saved timer cadence or the UI is on screen with nothing running.
  * "+Drive formatted" is replaced by "+Drive MOUNTED". Upstream counts
    overlay bytes written to sector 0, which says the firmware wrote a
    header. On mk1 the interesting failure was subtler and silent: the
    firmware read the card, rejected it as an unknown part, and skipped the
    mount entirely, leaving the volume unmounted and every inode invalid.
    Bytes on the card would have looked fine throughout. So check the mount
    flag and the in-RAM inode bitmap the mount fills.
  * The thresholds are re-measured for this panel and this ladder; the
    numbers upstream uses are its own. See the constants.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, panel, symbols, tasks                      # noqa: E402
from emu.longrun import spin                                       # noqa: E402
from emu.uiresume import open_snapshot                             # noqa: E402

# Measured on this build, on the 128x64 panel (8192 pixels). The pages the
# UI actually reaches light: main/SRC page 1873 (22.9%), SETTINGS list 1534
# (18.7%), SETTINGS with a lower row selected 1544 (18.8%). The frames that
# mean "not there yet" light far less: the early boot frame 227 (2.8%), the
# "+DRIVE" progress overlays 528 (6.4%) and the empty browser 985 (12.0%).
# 0.15 sits clear of every real page and above every progress frame. The
# browser at 12.0% is deliberately below the line -- it is a page the UI can
# legitimately be on, but it is not evidence the boot completed, and this
# check is about the boot.
MAIN_SCREEN_ON_FRACTION = 0.15

# mk1 addresses, from the ekFS work; see DIGITAKT-MK1.md. FUN_400d0f9c sets
# the flag on a successful mount and fills the inode bitmap from the card.
# They are literals rather than profile symbols because nothing else needs
# them resolved per-image yet -- if a second mk1 build ever appears, they
# belong in emu/symbols.py.
MOUNTED_FLAG = 0x420EDC50
INODE_BITMAP = 0x426876B0
INODE_BITMAP_BYTES = 0x2000


def frame_on_fraction(buf):
    """-> lit fraction of the 128x64 1bpp panel frame, using the same bit
    layout panel.lit() decodes."""
    if not buf:
        return 0.0
    return len(panel.lit(buf)) / float(panel.W * panel.H)


def u32(m, addr):
    try:
        return struct.unpack('>I', bytes(m.uc.mem_read(addr, 4)))[0]
    except Exception:                                              # noqa: BLE001
        return None


def bitmap_bits(m, addr, n):
    try:
        return sum(bin(b).count('1') for b in bytes(m.uc.mem_read(addr, n)))
    except Exception:                                              # noqa: BLE001
        return -1


def setup(snapshot, syx):
    """Build a Machine with the mk1 intro policy. -> (m, ev, pc, at, pits,
    profile)."""
    profile = symbols.resolve(open(config.main_image(), 'rb').read())
    flags = dict(unblock=True, softfloat=True, bitmap=True, dsp=True,
                 sdgate=True, esdhc=True)
    if profile.frame_sem is not None:
        # Left alone deliberately: see the module docstring.
        flags['unblock_except'] = (profile.frame_sem,)
    # Go through emu/uiresume.py rather than building timers here. A snapshot
    # taken mid-run carries its own timer cadence and must be resumed with
    # it -- constructing fresh Dtims wipes the DTIM3 the firmware armed for
    # itself, and the symptom is a snapshot whose framebuffer still holds a
    # perfectly good UI while nothing executes. A cold-ladder rung has no such
    # state and wants fresh timers. open_snapshot knows which is which; doing
    # it by hand here got a "Pits checkpoint configuration mismatch" on
    # gui.snap, which is that module's whole reason for existing.
    m, ev, st, pc, inq, at, pits = open_snapshot(
        snapshot, syx, profile, verbose=False, **flags)
    return m, ev, pc, at, pits, profile


def run(args):
    from unicorn import UC_HOOK_BLOCK

    m, ev, pc, at, pits, profile = setup(args.snapshot, args.syx)

    # The liveness signal that matters on this port. A wedged UI task parks
    # in the firmware's abort handler -- an empty `while (true)` -- while
    # interrupts carry on ticking, so timers, task counts and the framebuffer
    # all keep looking healthy and only this stops. It is what the GUI status
    # line shows and what the samples-folder freeze was diagnosed with.
    mainloop = getattr(profile, 'mainloop', None)
    hits = [0]
    if mainloop:
        def count(uc, address, size, user):
            if address == mainloop:
                hits[0] += 1
        m.uc.hook_add(UC_HOOK_BLOCK, count)

    # Latch whole frames: panel.read at an arbitrary instant returns a frame
    # torn on a page boundary, and the diff entry is the one instant the
    # front buffer is complete (see emu/panel.py).
    last = {'buf': None}
    if getattr(profile, 'panel_diff', None) and profile.fb_front is not None:
        def latch(uc, a, s, d):
            last['buf'] = panel.read(m, profile.fb_front)
        at(profile.panel_diff, latch)

    pc, done, stop = spin(m, pc, args.instrs, pits=pits, fast=True)

    frame = last['buf'] or panel.read(m, profile.fb_front)
    frac = frame_on_fraction(frame)
    lit = len(panel.lit(frame)) if frame else 0
    mounted = u32(m, MOUNTED_FLAG)
    inodes = bitmap_bits(m, INODE_BITMAP, INODE_BITMAP_BYTES)

    # Count the tasks the SCHEDULER holds, not the ones created during this
    # run. Upstream uses len(ev['tasks']), which only counts task_create going
    # by -- on a settled snapshot every task was created long before the
    # snapshot and that count is 0, which says nothing about whether the
    # system is alive. emu/tasks.py walks the priority array instead, which
    # is also why it exists (mk1 snapshots carry no task map).
    live = tasks.priority_lists(m, profile.prio_heads, profile.current_tcb)
    ntasks = sum(len(chain) for _prio, chain in live)

    skip = ev.get('unblock_skip', set())
    by_sem = ev.get('satisfied_by_sem', {})
    # frame_sem is a known, documented residual rather than a bug: it only
    # joins `skip` when intro_done's hook fires, and on a rung taken after
    # the intro that hook never fires again while the intro's exit loop goes
    # on pending the same semaphore. unblock keeps faking it, harmlessly.
    residual = {profile.frame_sem} if profile.frame_sem is not None else set()
    escaped = {s: n for s, n in by_sem.items()
               if s in skip and s not in residual}

    reasons, ok = [], True

    def check(name, cond, detail=''):
        nonlocal ok
        ok = ok and bool(cond)
        reasons.append('%s: %s%s' % ('PASS' if cond else 'FAIL', name,
                                     (' -- %s' % detail) if detail else ''))

    check('run completed the budget', stop == 'limit', 'stop=%r' % stop)
    check('a real UI page is on screen',
          frac >= MAIN_SCREEN_ON_FRACTION,
          'lit=%d on_fraction=%.3f (need >= %.2f)'
          % (lit, frac, MAIN_SCREEN_ON_FRACTION))
    # Measured on the settled snapshot: ~6.4 main-loop entries per million
    # instructions. One per ten million is a floor two orders of magnitude
    # below that -- it separates "running" from "stopped", which is the
    # distinction this check is for, and does not pretend to judge speed.
    want = max(1, done // 10_000_000)
    check('the main loop is still being entered',
          mainloop is not None and hits[0] >= want,
          '%d entries over %d instrs (need >= %d)' % (hits[0], done, want)
          if mainloop else 'profile.mainloop did not resolve')
    check('the scheduler holds a runnable task', ntasks >= args.min_tasks,
          'tasks=%d in %d priority slot(s) (need >= %d)'
          % (ntasks, len(live), args.min_tasks))
    check('+Drive mounted', mounted == 1, 'mounted flag = %r' % mounted)
    check('mount populated the inode bitmap', inodes > 0,
          '%d bits set' % inodes)
    check('no faked pend escaped unblock_skip', not escaped,
          'escaped=%r' % {('0x%08x' % s): n for s, n in escaped.items()})

    print('%s: %d instrs, stop=%s, mainloop=%d, tasks=%d, lit=%d (%.3f), '
          'mounted=%s, inodes=%d, pends faked=%d over %d never-fake sems'
          % ('PASS' if ok else 'FAIL', done, stop, hits[0], ntasks, lit,
             frac, mounted, inodes, ev.get('satisfied', 0), len(skip)))
    for r in reasons:
        print('  ' + r)
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--snapshot', required=True, help='ladder rung to resume')
    ap.add_argument('--syx', default='Digitakt_OS1.53.syx')
    ap.add_argument('--instrs', type=int, default=60_000_000)
    # The priority array holds RUNNABLE tasks; pend_b unlinks a task that
    # blocks. With unblock on, little ever blocks, so this count is small and
    # varies -- 2 on the settled snapshot. It is a floor against "nothing is
    # runnable at all", not a census.
    ap.add_argument('--min-tasks', type=int, default=1)
    args = ap.parse_args()
    return run(args)


if __name__ == '__main__':
    sys.exit(main())
