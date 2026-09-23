#!/usr/bin/env python3
"""Measure panel-input delivery latency and encoder batching, headless.

Drives emu.gui.Emulator exactly as the panel window does -- events appended
to its inbox, delivered by _drain_input at chunk boundaries -- and reads the
worker's own `[gui] input --feed <instr>:<hex>` lines back. No Tk involved.

Two measurements, each in EMULATED time (instructions / ips):
  press+release of STOP 100 ms apart in wall time -- when does each land?
  20 encoder detents 50 ms apart -- how many packets, how many detents each?
"""
import io
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, run as _run                     # noqa: E402
from emu.gui import Emulator                            # noqa: E402

FEED = re.compile(r'input --feed (\d+):([0-9a-f]+)')


class Tee(io.TextIOBase):
    def __init__(self, *sinks):
        self.sinks = sinks

    def write(self, s):
        for k in self.sinks:
            k.write(s)
        return len(s)

    def flush(self):
        for k in self.sinks:
            k.flush()


def feeds_since(buf, mark):
    return [(int(n), h) for n, h in FEED.findall(buf.getvalue()[mark:])]


def main():
    snap, _prefix = _run.paths_for(config.firmware(None))
    buf = io.StringIO()
    sys.stdout = Tee(sys.__stdout__, buf)
    emu = Emulator(snap, syx=None)
    emu.start()
    if not emu.ready.wait(300):
        raise SystemExit('worker never became ready')
    time.sleep(3)
    ips = getattr(emu.device, 'post_intro_ips', 0) or 132_000_000
    sec = lambda n: n / ips                              # noqa: E731

    # -- button: press, 100 ms later release
    mark = len(buf.getvalue())
    t0 = emu.stats['instrs']
    emu.inbox.append(('press', 11, 0))
    time.sleep(0.10)
    emu.inbox.append(('release', 11, 0))
    time.sleep(5)
    f = feeds_since(buf, mark)
    print('')
    print('=== BUTTON (STOP): appended at %dM instr' % (t0 // 1_000_000))
    for i, (n, h) in enumerate(f[:2]):
        print('   %-7s delivered at %dM  +%.2fs emulated  payload %s'
              % (('press', 'release')[i], n // 1_000_000, sec(n - t0), h))
    if len(f) >= 2:
        print('   press->release gap: %.2fs emulated' % sec(f[1][0] - f[0][0]))

    # -- encoders: 20 detents, 50 ms apart in wall time (1.0 s total)
    # A knob the device maps to a wire channel, not merely a control the
    # firmware's table calls an encoder: the panel's A..H are what a person
    # actually turns.
    mapped = {c: n for c, n in emu.encoder_names.items()
              if emu.device.encoder_channel(c) is not None}
    print('   mapped encoders: %s' % sorted(mapped.items()))
    code = next((c for c, n in mapped.items() if n == 'A'), None)
    if code is None:
        code = sorted(mapped)[0]
    mark = len(buf.getvalue())
    t0 = emu.stats['instrs']
    for _ in range(20):
        emu.inbox.append(('encoder', code, 1))
        time.sleep(0.05)
    time.sleep(5)
    f = feeds_since(buf, mark)
    sizes = [len(h) // 4 for _n, h in f]                 # 2 bytes per detent
    print('=== ENCODER %r: 20 detents over 1.0s wall, appended from %dM'
          % (emu.encoder_names[code], t0 // 1_000_000))
    print('   packets: %d   detents per packet: %s' % (len(f), sizes))
    if f:
        print('   first detent landed +%.2fs, last +%.2fs emulated after the first append'
              % (sec(f[0][0] - t0), sec(f[-1][0] - t0)))

    emu.stop_flag.set()
    emu.pause.clear()
    emu.join(15)


if __name__ == '__main__':
    main()
