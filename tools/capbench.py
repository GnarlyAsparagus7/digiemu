#!/usr/bin/env python3
"""How much faster than real time can live audio run? Unpaced capacity bench.

Runs the GUI's own emulator thread headless with a stub sound card, plays
the pattern tools/livecheck.py uses (STOP, STOP, sample on track 1, trigs
1/5/9/13, PLAY), then turns pacing off (`Emulator.realtime = False`) and
measures over a fixed wall-clock window:

    capacity   emulated seconds per wall second (1.00 = exactly real time)
    audio      seconds of audio rendered per wall second
    peak, rms  of the audio rendered in the window (16-bit), to show the
               tracks asked for are actually playing

A/B two trees by running each in alternating runs: thermals drift.

    python tools/capbench.py --snapshot SNAP [--seconds 10] [--runs 3] [--profile OUT]
                             [--tracks N] [--steps 1,5,9,13]

`--tracks N` puts the same sample on tracks 1..N (TRK + trig selects a
track) and grid-records `--steps` on each, so the render has N voices to mix.
`--tracks 0` presses nothing: the snapshot as restored (loaded-sample.snap
restores playing its own pattern), which is what the GUI shows on opening.
`--screens DIR` saves the screen after each step of that, to check it.

`--profile` samples the emulator thread's stack (sys._current_frames) and
writes a flat count of the innermost Python function per sample.
"""
import argparse
import collections
import json
import os
import shutil
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sampler(thread, stop, out, interval=0.001):
    """Count the emulator thread's innermost frame, and whether it is inside
    emu_start (the engine) or in Python between steps."""
    counts = collections.Counter()
    where = collections.Counter()
    while not stop.is_set():
        frame = sys._current_frames().get(thread.ident)
        if frame is not None:
            code = frame.f_code
            counts['%s:%s' % (os.path.basename(code.co_filename), code.co_name)] += 1
            f, inside = frame, False
            while f is not None:
                if f.f_code.co_name == 'emu_start':
                    inside = True
                    break
                f = f.f_back
            if not inside:
                where['python between steps'] += 1
            elif code.co_name == 'emu_start':
                where['engine (C)'] += 1
            else:
                where['python hooks inside emu_start'] += 1
        time.sleep(interval)
    out['functions'] = counts
    out['where'] = where


def run_once(snapshot, card, seconds, profile, tracks=1, steps=(1, 5, 9, 13),
             screens=None):
    from emu import gui
    sys.path.insert(0, os.path.join(ROOT, 'tools'))
    import livecheck
    gui.audioout.WaveOut = livecheck.StubOut
    # Paced while the keys go in: unpaced, a 0.15 s press lasts several
    # emulated tenths and DOWN auto-repeats onto an empty slot, where YES
    # opens the file browser instead of assigning the sample.
    emu = gui.Emulator(snapshot, realtime=True)
    emu.start()
    emu.ready.wait(180)

    def tap(code, hold=0.15, after=0.4):
        emu.inbox.append(('press', code, 0))
        time.sleep(hold)
        emu.inbox.append(('release', code, 0))
        time.sleep(after)

    def shot(name):
        if screens and emu._panel_latch is not None:
            from emu import panel
            panel.write_png(emu._panel_latch, os.path.join(screens, name + '.png'),
                            scale=3)

    time.sleep(1.0)
    if tracks:
        tap(11)
        tap(11)                              # STOP (the snapshot restores playing)
        shot('00-stopped')
    for track in range(tracks):
        if track:
            emu.inbox.append(('press', 2, 0))    # TRK held + trig N: track N
            time.sleep(0.15)
            tap(24 + track)
            emu.inbox.append(('release', 2, 0))
            time.sleep(0.4)
            shot('t%d-1-selected' % (track + 1))
        tap(21)                              # FLTR, so that SRC opens the
        tap(20)                              # SRC parameters, not the waveform
        emu.inbox.append(('encoder', 4, 1))  # D: opens the slot list
        time.sleep(0.4)
        if track:
            # The list opens at the track's own slot (35..72 on the empty
            # tracks 2..8), and neither knob D nor FUNC+UP pages it: hold UP,
            # which auto-repeats, until it stops at the top (0: OFF).
            tap(14, hold=14.0)
        tap(15)                              # DOWN: slot 1
        tap(12, after=0.6)                   # YES: the sample on this track
        shot('t%d-2-sample' % (track + 1))
        tap(9)                               # RECORD
        for step in steps:
            tap(23 + step)
        shot('t%d-3-recorded' % (track + 1))
        tap(9)
    if tracks:
        tap(10, after=0.5)                   # PLAY
    shot('99-playing')
    emu.realtime = False                     # unpaced from here on
    time.sleep(1.0)                          # let it settle

    prof, stop = {}, threading.Event()
    if profile:
        t = threading.Thread(target=sampler, args=(emu, stop, prof), daemon=True)
        t.start()
    emu.audio_clear()
    i0, f0, t0 = emu.stats['instrs'], emu.audio_frames, time.perf_counter()
    time.sleep(seconds)
    i1, f1, t1 = emu.stats['instrs'], emu.audio_frames, time.perf_counter()
    pcm = emu.audio_take()
    stop.set()
    ips = emu._pits.sources[0].ips
    emu.stop_flag.set()
    emu.join(15)
    wall = t1 - t0
    samples = memoryview(pcm).cast('h') if len(pcm) >= 2 else []
    peak = max((abs(v) for v in samples), default=0)
    rms = (sum(v * v for v in samples) / len(samples)) ** 0.5 if len(samples) else 0.0
    return {'capacity': (i1 - i0) / ips / wall,
            'audio': (f1 - f0) / 48000 / wall,
            'ips': ips, 'wall': wall, 'peak': peak, 'rms': rms,
            'tracks': tracks, 'profile': prof}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--card', default=os.path.join(ROOT, 'plusdrive.img'))
    ap.add_argument('--seconds', type=float, default=10)
    ap.add_argument('--runs', type=int, default=1)
    ap.add_argument('--profile', help='write a stack-sample profile (JSON) here')
    ap.add_argument('--json', help='append each run as a JSON line here')
    ap.add_argument('--tracks', type=int, default=1,
                    help='play the sample on tracks 1..N (default 1)')
    ap.add_argument('--steps', default='1,5,9,13',
                    help='steps to grid-record on each track')
    ap.add_argument('--screens', help='save the screen after each programming step here')
    args = ap.parse_args()

    # One cached card copy for every invocation: the emulator maps the image,
    # so a copy cannot be deleted while the process lives, and a copy per run
    # fills the disk (1 GB each). Refreshed when the source card changes.
    card = os.path.join(tempfile.gettempdir(), 'capbench-card.img')
    src = os.stat(args.card)
    try:
        dst = os.stat(card)
        stale = dst.st_size != src.st_size or dst.st_mtime < src.st_mtime
    except FileNotFoundError:
        stale = True
    if stale:
        shutil.copyfile(args.card, card + '.part')
        os.replace(card + '.part', card)
    os.environ['DT2_PLUSDRIVE'] = card
    sys.path.insert(0, ROOT)
    os.chdir(ROOT)
    snapshot = os.path.abspath(args.snapshot)
    for k in range(args.runs):
        r = run_once(snapshot, card, args.seconds, args.profile and k == args.runs - 1,
                     args.tracks, [int(x) for x in args.steps.split(',')],
                     args.screens)
        print('run %d: capacity %.3fx real time, audio %.3fx  (ips %dM, %.1f s)  '
              'tracks %d  peak %d  rms %.0f'
              % (k + 1, r['capacity'], r['audio'], r['ips'] // 1_000_000, r['wall'],
                 r['tracks'], r['peak'], r['rms']),
              flush=True)
        if args.json:
            with open(args.json, 'a') as fh:
                fh.write(json.dumps({k2: v for k2, v in r.items() if k2 != 'profile'}) + '\n')
        if r['profile']:
            p = r['profile']
            tot = sum(p['where'].values()) or 1
            print('  ' + ', '.join('%s %.0f%%' % (k2, 100 * n / tot)
                                   for k2, n in p['where'].most_common()))
            for name, n in p['functions'].most_common(25):
                print('  %5.1f%%  %s' % (100 * n / tot, name))
            with open(args.profile, 'w') as fh:
                json.dump({'where': p['where'], 'functions': p['functions']}, fh, indent=1)


if __name__ == '__main__':
    main()
