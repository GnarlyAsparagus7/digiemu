#!/usr/bin/env python3
"""Headless check of live audio and pattern playback through the GUI's own
emulator thread. Nothing is played aloud: the sound card is a stub that
consumes 48 kHz of wall time.

    python tools/livecheck.py --snapshot SNAP [--card IMG] [--pattern]

Without --pattern: selects the loaded sample on track 1 (SRC, encoder D,
DOWN, YES), triggers it, and records. With --pattern: STOP (a snapshot can
restore playing, and PLAY while playing pauses), the same sample selection,
grid-records trigs on steps 1/5/9/13, PLAY, and checks the recording for hits
on the tempo grid. Prints real-time %, buffered ms and dropouts once a second.

The card is copied first, so the image the GUI or another run holds is never
written. The key sequence assumes the sample is in slot 1 of the project.
"""
import argparse
import math
import os
import shutil
import struct
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class StubOut:
    """Stands in for audioout.WaveOut: drains at the device rate in wall
    time, and drops what overflows its buffers, like the real one."""

    def __init__(self, rate, channels, buffers=40, block_ms=10):
        self.rate, self.block_ms, self.cap = rate, block_ms, buffers
        self.bytes = self.dropped = 0
        self.t0, self.fed = None, 0.0
        self.pending = bytearray()

    def queued(self):
        if self.t0 is None:
            return 0
        left = self.fed - (time.time() - self.t0)
        return max(0, int(left * 1000 / self.block_ms))

    def write(self, pcm, block=False, abort=None):
        now = time.time()
        if self.t0 is None or self.queued() == 0:
            self.t0, self.fed = now, 0.0
        room = self.cap - self.queued()
        self.pending += pcm
        size = self.rate * 4 * self.block_ms // 1000
        blocks = len(self.pending) // size
        del self.pending[:blocks * size]
        take = min(blocks, room)
        self.dropped += blocks - take
        self.fed += take * self.block_ms / 1000
        self.bytes += len(pcm)

    def close(self):
        pass


def grid_levels(pcm, rate, bpm, first):
    """-> [(t, rms of the first 20 ms)] for each beat from `first` on."""
    n = len(pcm) // 4
    s = struct.unpack('<%dh' % (2 * n), pcm[:n * 4])

    def rms(t0, t1):
        a, b = max(0, int(t0 * rate)), min(n, int(t1 * rate))
        v = [(s[2 * i] + s[2 * i + 1]) / 2 for i in range(a, b)]
        return math.sqrt(sum(x * x for x in v) / len(v)) if v else 0.0

    out, t = [], first
    while t + 0.03 < n / rate:
        out.append((t, rms(t, t + 0.02), rms(t + 30 / bpm, t + 30 / bpm + 0.02)))
        t += 60 / bpm
    return out


def first_onset(pcm, rate, level):
    n = len(pcm) // 4
    s = struct.unpack('<%dh' % (2 * n), pcm[:n * 4])
    for i in range(0, n - 48, 48):
        v = [(s[2 * j] + s[2 * j + 1]) / 2 for j in range(i, i + 48)]
        if math.sqrt(sum(x * x for x in v) / 48) > level:
            return i / rate
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--card', default=os.path.join(ROOT, 'plusdrive.img'))
    ap.add_argument('--pattern', action='store_true')
    ap.add_argument('--seconds', type=int, default=6)
    ap.add_argument('--bpm', type=float, default=98.0,
                    help="the project's tempo, for the grid check")
    ap.add_argument('--wav', help='save the recording here')
    args = ap.parse_args()

    # One fixed copy, refreshed every run: the emulator maps the image, so a
    # per-run copy cannot be deleted while this process lives and each run
    # would leave 1 GB behind.
    card = os.path.join(tempfile.gettempdir(), 'livecheck-card.img')
    shutil.copyfile(args.card, card)
    os.environ['DT2_PLUSDRIVE'] = card
    sys.path.insert(0, ROOT)
    os.chdir(ROOT)
    from emu import audioout, gui

    gui.audioout.WaveOut = StubOut
    emu = gui.Emulator(os.path.abspath(args.snapshot))
    emu.start()
    emu.ready.wait(180)
    if not emu.audio_live:
        print('audio is not live (%s); the accelerated Unicorn is needed'
              % (emu.audio_error or 'fallback mode'))

    def tap(code, hold=0.15, after=0.4):
        emu.inbox.append(('press', code, 0))
        time.sleep(hold)
        emu.inbox.append(('release', code, 0))
        time.sleep(after)

    time.sleep(1.5)
    if args.pattern:
        tap(11)
        tap(11)                                  # STOP
    tap(20)                                      # SRC
    emu.inbox.append(('encoder', 4, 1))          # D: the sample slot list
    time.sleep(0.6)
    tap(15)                                      # DOWN
    tap(12, after=1.0)                           # YES
    if args.pattern:
        tap(9)                                   # RECORD: grid recording
        for step in (0, 4, 8, 12):
            tap(24 + step)
        tap(9)
    emu.audio_clear()
    f0, t0 = emu.audio_frames, time.time()
    tap(10 if args.pattern else 24, after=0.0)   # PLAY, or trig 1
    for _ in range(args.seconds):
        time.sleep(1.0)
        print('  %4.1f s: audio %.2f s  real %.0f%%  buffered %d ms  '
              'dropouts %d' % (time.time() - t0,
                               (emu.audio_frames - f0) / 48000,
                               emu.stats.get('real', 0) * 100,
                               emu.live_latency_ms(), emu.live_underruns),
              flush=True)
    if args.pattern:
        tap(11)
    pcm = emu.audio_take()
    emu.stop_flag.set()
    emu.join(15)
    if args.wav:
        audioout.write_wav(args.wav, pcm)
    peak = max((abs(v) for v in struct.unpack(
        '<%dh' % (len(pcm) // 2), pcm)), default=0)
    print('recording %.2f s, peak %d' % (len(pcm) / 4 / 48000, peak))
    if not args.pattern or not peak:
        return 0 if peak else 1
    start = first_onset(pcm, 48000, 400)
    beats = grid_levels(pcm, 48000, args.bpm, start)
    hits = [b for b in beats if b[1] > 4 * max(b[2], 1)]
    print('beats on the %.1f BPM grid from %.3f s: %d, with a hit: %d' % (
        args.bpm, start, len(beats), len(hits)))
    for t, on, off in beats:
        print('  %.3f s  on-beat %5.0f  half-beat later %5.0f' % (t, on, off))
    return 0 if beats and len(hits) == len(beats) else 1


if __name__ == '__main__':
    sys.exit(main())
