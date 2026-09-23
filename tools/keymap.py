#!/usr/bin/env python3
"""Identify every panel code by what it DOES, not by what a table calls it.

devices/digitakt.toml assigns codes to buttons using the 48-entry table at
0x4018E254, and that table labels the hardware PANEL-TEST screen rather than
the runtime key map. Measured, eleven codes already disagree: the table calls
code 19 "FUNC" and it selects the TRIG page; it calls code 10 "11" and it is
PLAY. So the GUI's PLAY button sends code 25, which is a trig.

Two records in this project's own notes contradict each other about the trig
codes, which is exactly why this has to be measured rather than reasoned out.

Each code is pressed from a FRESH resume, because presses are stateful and
pressing forty-eight things into one machine measures the forty-eighth in the
context of the other forty-seven. Per code it records:

  lit        pixels lit on the resulting untorn frame, and whether it changed
  transport  the transport flag at 0x4199DC2C (1 playing / 2 stopped), which
             is what identifies PLAY and STOP regardless of what is drawn
  png        the screen, so pages can be told apart by eye

Uses gui.snap and the GUI's own build flags, so what this measures is what a
person clicking the window gets.
"""
import argparse
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, panel, symbols
from emu.dtmap import wire_for
from emu.uiresume import open_snapshot

GUI_FLAGS = dict(unblock=True, softfloat=True, bitmap=True, dsp=True)
TRANSPORT_FLAG = 0x4199DC2C   # Digitakt mk1 OS 1.53 fallback only; rebound from
                              # prof.transport_state once the image is resolved


MAIN_UI_LIT = 1200          # frames below this are the +Drive overlay


def last_main_frame(cap):
    """-> the most recent MAIN-UI frame, skipping +Drive overlay frames.

    The overlay is redrawn over the main page about a third of the time, so
    comparing whatever frame happens to be last measures the overlay's phase
    rather than the effect of the press. Only frames above MAIN_UI_LIT are the
    main page.
    """
    for buf in reversed(cap.frames):
        if len(panel.lit(buf)) > MAIN_UI_LIT:
            return buf
    return cap.frames[-1] if cap.frames else None


def u32(m, a):
    try:
        return struct.unpack('>I', m.uc.mem_read(a, 4))[0]
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot', default='snapshots/Digitakt_OS1.53/gui.snap')
    ap.add_argument('--codes', default='0-47')
    ap.add_argument('--settle', type=lambda s: int(s, 0), default=8_000_000)
    ap.add_argument('--probe', type=lambda s: int(s, 0), default=12_000_000)
    ap.add_argument('--out', default='out/keymap')
    args = ap.parse_args()

    lo, hi = (int(x) for x in args.codes.split('-'))
    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)
    global TRANSPORT_FLAG
    TRANSPORT_FLAG = prof.transport_state or TRANSPORT_FLAG   # resolved per image
    os.makedirs(args.out, exist_ok=True)

    flags = dict(GUI_FLAGS)
    if prof.frame_sem is not None:
        flags['unblock_except'] = (prof.frame_sem,)

    rows = []
    print('%-5s %-8s %-8s %-10s %-10s %s'
          % ('code', 'ch/bit', 'lit', 'changed', 'transport', 'png'))
    for code in range(lo, hi + 1):
        wire = wire_for(code)
        if wire is None:
            print('%-5d (not on this panel)' % code)
            continue
        ch, bit = wire
        try:
            m, ev, st, pc, inq, at, pits = open_snapshot(
                args.snapshot, args.syx, prof, verbose=False, **flags)
        except Exception as exc:                         # noqa: BLE001
            print('%-5d resume failed: %s' % (code, exc))
            continue
        cap = panel.Capture(at, diff_addr=getattr(prof, 'panel_diff', None),
                            front_addr=prof.fb_front)
        pc, _e, _w = longrun.spin(m, pc, args.settle, pits=pits)
        base = last_main_frame(cap)
        base_lit = len(panel.lit(base)) if base else -1
        t0 = u32(m, TRANSPORT_FLAG)

        from emu import panelin
        pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 1 << bit))
        pc, _e, _w = longrun.spin(m, pc, args.probe, pits=pits)
        pc = panelin.feed(m, prof, panelin.encode_buttons(ch, 0))
        pc, _e, _w = longrun.spin(m, pc, args.probe, pits=pits)

        now = last_main_frame(cap)
        lit = len(panel.lit(now)) if now else -1
        t1 = u32(m, TRANSPORT_FLAG)
        changed = bool(now is not None and base is not None and now != base)
        png = os.path.join(args.out, 'code%02d.png' % code)
        if now:
            panel.write_png(now, png, scale=6)
        rows.append({'code': code, 'channel': ch, 'bit': bit, 'lit': lit,
                     'base_lit': base_lit, 'changed': changed,
                     'transport_before': t0, 'transport_after': t1,
                     'transport_changed': t0 != t1})
        print('%-5d %d/%-6d %-8d %-10s %-10s %s'
              % (code, ch, bit, lit, 'YES' if changed else 'no',
                 ('%s->%s' % (t0, t1)) if t0 != t1 else str(t1),
                 os.path.basename(png)), flush=True)

    with open(os.path.join(args.out, 'keymap.json'), 'w',
              encoding='utf-8') as fh:
        json.dump({'snapshot': args.snapshot, 'rows': rows}, fh, indent=2)
    print('')
    print('transport keys (the flag moved):')
    for r in rows:
        if r['transport_changed']:
            print('   code %-3d  %s -> %s'
                  % (r['code'], r['transport_before'], r['transport_after']))


if __name__ == '__main__':
    main()
