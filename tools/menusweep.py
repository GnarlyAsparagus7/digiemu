#!/usr/bin/env python3
"""Every code, pressed inside the settings list, judged by page set.

The method is now validated: on the settled snapshot GLOBAL opens the list
(page 1534 appears) and a second GLOBAL closes it (back to 1873 alone). So a
sequence of presses does land, and comparing the SET of pages over a window
is immune to the flicker that made the earlier sweeps meaningless.

For each code: resume, open the list, press the code, and report the pages.
A cursor key should leave 1534 and add nothing. A confirm key should replace
1534 with something else. Anything that does nothing leaves the set alone.
"""
import collections
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from emu import config, longrun, panel, panelin, symbols           # noqa: E402
from emu.dtmap import wire_for                                     # noqa: E402
from emu.uiresume import open_snapshot                             # noqa: E402

SNAP = 'snapshots/Digitakt_OS1.53/gui.snap'
GLOBAL = 6
WINDOW = 20_000_000
CODES = range(48)

img = open(config.main_image(), 'rb').read()
prof = symbols.resolve(img, load_addr=0x40000400)
flags = dict(unblock=True, softfloat=True, bitmap=True, dsp=True)
if prof.frame_sem is not None:
    flags['unblock_except'] = (prof.frame_sem,)


class Held:
    def __init__(self):
        self.mask = {}

    def set(self, code, down):
        ch, bit = wire_for(code)
        v = self.mask.get(ch, 0)
        v = (v | (1 << bit)) if down else (v & ~(1 << bit))
        self.mask[ch] = v
        return panelin.encode_buttons(ch, v)


print('%-5s %-8s %-22s %s' % ('code', 'ch/bit', 'pages after', 'verdict'),
      flush=True)
for code in CODES:
    if code == GLOBAL:
        continue
    try:
        ch, bit = wire_for(code)
    except Exception:                                              # noqa: BLE001
        continue
    m, ev, st, pc, inq, at, pits = open_snapshot(SNAP, None, prof,
                                                 verbose=False, **flags)
    cap = panel.Capture(at, diff_addr=getattr(prof, 'panel_diff', None),
                        front_addr=prof.fb_front)
    h = Held()

    def spin(n):
        global pc
        pc, ex, _w = longrun.spin(m, pc, n, pits=pits, fast=True)

    def pages():
        c = collections.Counter(len(panel.lit(f)) for f in cap.frames)
        del cap.frames[:]
        return c

    def tap(c):
        global pc
        pc = panelin.feed(m, prof, h.set(c, True))
        spin(2_000_000)
        pc = panelin.feed(m, prof, h.set(c, False))
        spin(WINDOW)

    spin(15_000_000)
    pages()
    tap(GLOBAL)
    inmenu = set(pages())
    tap(code)
    after = set(pages())
    if 1534 not in inmenu:
        verdict = 'the menu did not open; ignore'
    elif after == inmenu:
        verdict = 'nothing'
    elif 1534 not in after:
        verdict = 'LEFT THE LIST  <-- confirm or cancel'
    else:
        verdict = 'changed while still in the list  <-- cursor?'
    print('%-5d %-8s %-22s %s'
          % (code, '%d/%d' % (ch, bit), str(sorted(after))[:22], verdict),
          flush=True)
    del m
