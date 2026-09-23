"""A Digitakt-shaped front panel, drawn to look like the hardware.

emu/gui.py lays controls out automatically from the device file's groups,
which draws any product correctly but looks like a debugger. This draws the
mk1's actual faceplate: the OLED at its real aspect, eight encoders in two
rows, the page column down the right, the function column down the left and
sixteen trig keys across the bottom.

Positions live here because they are a fact about the plastic, not about the
image. Everything else still comes from the firmware: a control is placed by
looking up its own NAME in the image's control table, so a code that moves
between builds follows its label rather than silently landing in the wrong
hole. A label this layout does not mention is drawn in an overflow row
instead of being dropped.

MULTITOUCH. The wire carries an 8-bit STATE BITMASK per channel, not press
and release events, so simultaneous presses are what the hardware natively
expresses -- see emu/panelin.py. A plain click is momentary: press on down,
release on up. Shift-click LATCHES, so the button stays asserted while you
click others, which is how chords like FUNC+SRC, or holding a trig while
turning an encoder, are formed. Latched keys are drawn lit; "clear latched"
or Escape releases them all.

AUDIO. With the accelerated Unicorn (patches/README.md) the firmware's
render runs faster than real time and plays LIVE (MUTE silences it). Without
it the emulator records at a slow audio clock and PLAY plays the recording
back at 48 kHz afterwards. Either way the output is recorded: PLAY replays
it (silent ends trimmed), SAVE WAV writes it out. --no-audio skips the audio
model.

    uv run python -m emu.dtpanel [snapshot] [--no-audio]
"""
import math
import os
import sys
import time
import tkinter as tk

from emu import audioout, config
from emu.gui import ON, OFF, Emulator, H, W

# RGB for each framebuffer byte value: zero is off, anything else on.
_PIXEL = [ON if v else OFF for v in range(256)]

SCALE = 4
BG, FACE, EDGE = '#0b0d10', '#1c2027', '#2c323b'
TEXT, DIM, AMBER = '#c9d3e0', '#6b7789', '#ffb638'
LIT, REC_C, PLAY_C = '#3f4b5c', '#e2483d', '#3fbf6a'

# Key LEDs. An unlit key is still sent a colour -- palette 02, (1,1,1) of 31,
# the backlight glow -- so anything this dim is drawn as off. A lit key's face
# is the LED colour mixed into the key colour, like a backlit key cap.
LED_OFF = '#20252c'
LED_DARK = 24          # of 255: brighter than this counts as lit
LED_MIX = 0.6


def _mix(face, rgb, t):
    base = [int(face[i:i + 2], 16) for i in (1, 3, 5)]
    return tuple(round(b + (c - b) * t) for b, c in zip(base, rgb))


def _hex(rgb):
    return '#%02x%02x%02x' % tuple(rgb)


def _luma(rgb):
    r, g, b = rgb
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255

# Firmware label -> (x, y, w, h, secondary caption, tint).
BUTTONS = {
    # right-hand page column
    'TRIG': (1052, 96, 92, 42, 'Quantize', None),
    'SRC': (1052, 156, 92, 42, 'Assign', None),
    'FLTR': (1052, 216, 92, 42, 'Fltr Setup', None),
    'AMP': (1052, 276, 92, 42, 'Amp Env', None),
    'LFO': (1052, 336, 92, 42, 'LFO Setup', None),
    'PAGE': (1052, 410, 92, 38, None, None),
    # left function column
    'FUNC': (30, 406, 88, 40, None, AMBER),
    'BANK': (30, 462, 88, 40, 'Mute Mode', None),
    'PTN': (30, 518, 88, 40, 'Pattern Settings', None),
    'TRK': (30, 574, 88, 40, 'Track Settings', None),
    # menu row
    # The key the panel-test table calls PATTERN MENU is SONG on OS 1.5x:
    # held, the firmware shows "SONG MODE OFF". Named for what it does.
    'SONG': (150, 406, 104, 40, None, None),
    'GLOBAL': (264, 406, 92, 40, None, None),
    'SAMPLE': (366, 406, 92, 40, None, None),
    'TEMPO': (468, 406, 92, 40, None, None),
    # transport
    'STOP': (150, 470, 76, 46, None, None),
    'PLAY': (236, 470, 76, 46, None, PLAY_C),
    'RECORD': (322, 470, 76, 46, None, REC_C),
    # confirm and cursor
    'YES': (430, 470, 76, 46, 'Reload', None),
    'NO': (516, 470, 76, 46, 'tTime', None),
    'UP': (664, 464, 56, 40, None, None),
    'LEFT': (604, 510, 56, 40, None, None),
    'DOWN': (664, 510, 56, 40, None, None),
    'RIGHT': (724, 510, 56, 40, None, None),
    # The encoder PUSH switches. The firmware gives these their own button
    # codes (40..48) in the same table as everything else, but physically
    # they are the knobs above them, so they sit directly under each one and
    # double as its label rather than being exiled to an overflow row.
    'A': (596, 190, 48, 18, None, None),
    'B': (700, 190, 48, 18, None, None),
    'C': (804, 190, 48, 18, None, None),
    'D': (908, 190, 48, 18, None, None),
    'E': (596, 312, 48, 18, None, None),
    'F': (700, 312, 48, 18, None, None),
    'G': (804, 312, 48, 18, None, None),
    'H': (908, 312, 48, 18, None, None),
    'LEVEL/DATA': (1016, 538, 72, 18, None, None),
}
# sixteen trig keys, two rows of eight
for _i in range(16):
    BUTTONS[str(_i + 1)] = (150 + (_i % 8) * 108, 598 + (_i // 8) * 82,
                            98, 72, None, None)

# Firmware label -> (centre x, centre y, radius).
ENCODERS = {
    'A': (620, 150, 34), 'B': (724, 150, 34),
    'C': (828, 150, 34), 'D': (932, 150, 34),
    'E': (620, 272, 34), 'F': (724, 272, 34),
    'G': (828, 272, 34), 'H': (932, 272, 34),
    'LEVEL/DATA': (1052, 500, 30),
}


PANEL_W, PANEL_H = 1160, 790      # the drawn control surface


class DigitaktPanel(tk.Tk):
    def __init__(self, snapshot, syx=None, audio=True):
        super().__init__()
        self.title('digikit — Digitakt mk1 emulator (unofficial)')
        self.configure(bg=BG)
        self.canvas = tk.Canvas(self, width=PANEL_W, height=PANEL_H, bg=BG,
                                highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)

        self.emu = Emulator(snapshot, syx=syx, audio=audio)
        self.player = audioout.Player()
        self._audio_note = ('', 0.0)     # (message, shown until)
        self.held = set()        # codes currently asserted
        self.latched = set()     # subset of held that survives mouse-up
        self.codes = {}          # firmware label -> button code
        self.enc_codes = {}      # firmware label -> encoder code
        self.items = {}          # code -> (rect, text)
        self.text_fill = {}      # code -> the caption's own colour
        self.enc_items = {}      # code -> [ring, mark, angle]
        self._named = False
        self.led_of = {}         # button code -> LED id
        self.page_items = []     # (LED id, oval) for the pattern-page LEDs
        self._leds = {}          # LED id -> (r, g, b), as last drawn
        self._led_version = -1

        self.screen = tk.PhotoImage(width=W, height=H)
        self.big = tk.PhotoImage(width=W * SCALE, height=H * SCALE)
        self._draw_chrome()
        self.draw_screen(bytearray(W * H))

        self.bind('<Escape>', lambda _e: self.clear_latched())

        # Closing the window has to stop the worker BEFORE the interpreter
        # tears down. The worker sits inside uc_emu_start; if the main thread
        # exits first, weakref finalizers call Unicorn's release_handle and
        # free the handle out from under the running thread, and the process
        # dies -- SIGSEGV here, 'malloc(): unsorted double linked list
        # corrupted' on a longer run. Both arrived after billions of
        # instructions of clean operation, so they read as random instability
        # rather than as a shutdown bug. emu/gui.py's quit_all has carried
        # this fix for the other GUI all along; this one never got it.
        self.protocol('WM_DELETE_WINDOW', self.quit_all)

        # Ask for the front. A window manager opens a new window BEHIND the
        # focused one, so launched from a maximised editor this window is
        # created, mapped and on-screen but never seen -- which is
        # indistinguishable from "it didn't launch". Topmost is dropped again
        # straight away so the window behaves normally once it has been seen.
        # Place the window explicitly. Left to itself, Weston (WSLg) has put
        # it at +4368+1022 on a 5120x1440 desktop -- 1160x790 from there hangs
        # off the bottom-right corner, leaving a sliver under the taskbar --
        # and at +4082+215 on the launch before that. A window you cannot find
        # is indistinguishable from one that never opened, which is exactly
        # how this was reported. Clamp it fully on-screen.
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(0, min(80, sw - PANEL_W))
        y = max(0, min(60, sh - PANEL_H))
        self.geometry('%dx%d+%d+%d' % (PANEL_W, PANEL_H, x, y))
        print('[dtpanel] window %dx%d at +%d+%d on a %dx%d desktop'
              % (PANEL_W, PANEL_H, x, y, sw, sh), flush=True)

        self.lift()
        self.attributes('-topmost', True)
        self.after(500, lambda: self.attributes('-topmost', False))
        try:
            self.focus_force()
        except tk.TclError:          # no WM, or focus refused: not fatal
            pass

        self.emu.start()
        self.after(50, self.tick)

    # ---------------------------------------------------------------- chrome
    def _rr(self, x, y, w, h, r, **kw):
        """A rounded rectangle; Tk's canvas has no primitive for one."""
        pts = [x + r, y, x + w - r, y, x + w, y, x + w, y + r,
               x + w, y + h - r, x + w, y + h, x + w - r, y + h, x + r, y + h,
               x, y + h, x, y + h - r, x, y + r, x, y]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    def _draw_chrome(self):
        c = self.canvas
        self._rr(16, 14, 1128, 58, 10, fill='#101318', outline=EDGE)
        # The project's own name, not the product's branding: no logo mark,
        # wordmark or tagline from the hardware.
        c.create_text(46, 43, text='digikit', fill='#f2f5f9',
                      font=('Helvetica', 23, 'bold'), anchor='w')
        c.create_text(170, 45, text='Digitakt mk1 emulator · unofficial, not '
                      'affiliated with Elektron', fill=DIM,
                      font=('Helvetica', 11), anchor='w')
        c.create_text(1128, 45, text='Project: 001    120.0', fill=TEXT,
                      font=('Helvetica', 11), anchor='e')
        self._rr(24, 88, W * SCALE + 28, H * SCALE + 28, 8,
                 fill='#05070a', outline='#39414d')
        c.create_image(38, 102, image=self.big, anchor='nw')
        self.status = c.create_text(24, 768, text='booting...', fill=DIM,
                                    font=('Helvetica', 10), anchor='w')
        clear = c.create_text(1144, 768, text='clear latched', fill=DIM,
                              font=('Helvetica', 10), anchor='e')
        c.tag_bind(clear, '<Button-1>', lambda _e: self.clear_latched())
        self._draw_audio_controls()

    # ----------------------------------------------------------------- audio
    # The emulator records the audio output as it renders it -- far slower
    # than real time (see [audio] in devices/digitakt.toml) -- and these play
    # the recording back at the right rate: trigger a sound, let it render,
    # then PLAY.
    def _draw_audio_controls(self):
        c = self.canvas
        self.audio_text = c.create_text(536, 43, text='AUDIO  starting',
                                        fill=DIM, font=('Helvetica', 10),
                                        anchor='w')
        self.audio_btns = {}
        for name, x, w, fn in (('MUTE', 708, 58, self.audio_toggle_mute),
                               ('PLAY', 772, 58, self.audio_play),
                               ('CLEAR', 836, 58, self.audio_clear),
                               ('SAVE WAV', 900, 72, self.audio_save)):
            rect = self._rr(x, 30, w, 26, 6, fill=FACE, outline=EDGE)
            txt = c.create_text(x + w / 2, 43, text=name, fill=TEXT,
                                font=('Helvetica', 9, 'bold'))
            for item in (rect, txt):
                c.tag_bind(item, '<Button-1>', lambda _e, f=fn: f())
            self.audio_btns[name] = (rect, txt)

    def _note(self, msg, secs=4.0):
        self._audio_note = (msg, time.time() + secs)

    def _recording(self):
        """The recording with its silent ends trimmed, or b''."""
        emu = self.emu
        return audioout.trim_silence(emu.audio_take(),
                                     rate=emu.audio_cfg['rate'])

    def audio_play(self):
        emu = self.emu
        if not emu.audio_on:
            self._note('audio is off')
            return
        if self.player.playing:
            self.player.stop()
            self._note('stopped')
            return
        pcm = self._recording()
        if not pcm:
            self._note('only silence recorded so far')
            return
        rate = emu.audio_cfg['rate']
        if self.player.rate != rate:
            self.player = audioout.Player(rate=rate)
        self.player.play(pcm)
        self._note('playing %.2f s' % (len(pcm) / 4 / rate), 1.0)

    def audio_clear(self):
        self.player.stop()
        self.emu.audio_clear()
        self._note('recording cleared')

    def audio_toggle_mute(self):
        emu = self.emu
        if not emu.audio_live:
            self._note('no live audio: PLAY plays the recording')
            return
        emu.audio_mute(not emu.audio_muted)
        self._note('muted' if emu.audio_muted else 'live')

    def audio_save(self):
        emu = self.emu
        if not emu.audio_on:
            self._note('audio is off')
            return
        pcm = self._recording()
        if not pcm:
            self._note('only silence recorded so far')
            return
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension='.wav',
            filetypes=[('WAV audio', '*.wav')],
            initialfile=time.strftime('digitakt-%Y%m%d-%H%M%S.wav'))
        if not path:
            return
        audioout.write_wav(path, pcm, rate=emu.audio_cfg['rate'])
        self._note('saved %s' % os.path.basename(path))

    def _draw_audio_status(self):
        emu = self.emu
        playing = self.player.playing
        msg, until = self._audio_note
        if msg and time.time() < until:
            text = 'AUDIO  ' + msg
        elif playing:
            text = 'AUDIO  playing  (PLAY again stops)'
        elif emu.audio_on and emu.audio_live:
            if emu.audio_muted:
                text = 'AUDIO  LIVE, muted'
            elif emu._live_error:
                text = 'AUDIO  no output device'
            else:
                text = 'AUDIO  LIVE  ·  %d ms buffered' % (
                    emu.live_latency_ms())
                if emu.live_underruns:
                    text += '  ·  %d dropouts' % emu.live_underruns
        elif emu.audio_on:
            text = 'AUDIO  %.2f s recorded  ·  rendering at %.0f%%' % (
                emu.audio_seconds(), emu.audio_speed * 100)
        elif emu.audio_error:
            text = 'AUDIO  off: not set up in this snapshot'
        elif emu.ready.is_set():
            text = 'AUDIO  off'
        else:
            text = 'AUDIO  starting'
        if self.player.error:
            text = 'AUDIO  no output device: %s' % self.player.error
        self.canvas.itemconfigure(self.audio_text, text=text)
        self.canvas.itemconfigure(self.audio_btns['PLAY'][1],
                                  text='STOP' if playing else 'PLAY')
        self.canvas.itemconfigure(self.audio_btns['MUTE'][1],
                                  text='UNMUTE' if emu.audio_muted else 'MUTE')

    def draw_screen(self, fb):
        # Unchanged since the last refresh: nothing to do. Building the image
        # is a Python loop over every pixel, run on this thread while the
        # emulator thread waits for the interpreter lock, so skipping it on a
        # still screen buys emulation time.
        frame = bytes(fb)
        if frame == getattr(self, '_last_frame', None):
            return
        self._last_frame = frame
        body = b''.join(map(_PIXEL.__getitem__, frame))
        self.screen.put(b'P6\n%d %d\n255\n' % (W, H) + body, to=(0, 0, W, H))
        # copy -zoom writes into the existing image; PhotoImage.zoom would
        # allocate a new one on every refresh.
        self.tk.call(self.big, 'copy', self.screen, '-zoom', SCALE, SCALE)

    # --------------------------------------------------------------- widgets
    def _bind_button(self, item, code):
        self.canvas.tag_bind(item, '<ButtonPress-1>',
                             lambda e, k=code: self.press(k, e))
        self.canvas.tag_bind(item, '<ButtonRelease-1>',
                             lambda _e, k=code: self.release(k))

    def _build_controls(self):
        """Draw every control, once the firmware's own names are known."""
        c = self.canvas
        overflow = []
        for label, code in sorted(self.codes.items()):
            spec = BUTTONS.get(label)
            if spec is None:
                overflow.append((label, code))
                continue
            x, y, w, h, sub, tint = spec
            rect = self._rr(x, y, w, h, 7, fill=FACE, outline=EDGE)
            txt = c.create_text(x + w / 2, y + h / 2, text=label[:13],
                                fill=tint or TEXT,
                                font=('Helvetica', 10, 'bold'))
            if sub:
                c.create_text(x + w / 2, y + h + 11, text=sub, fill=DIM,
                              font=('Helvetica', 8))
            self.items[code] = (rect, txt)
            self.text_fill[code] = tint or TEXT
            self._bind_button(rect, code)
            self._bind_button(txt, code)

        # Anything the faceplate map does not mention still gets a key, so a
        # build with an extra control is usable rather than silently short.
        # Placed in the empty block right of the transport, NOT at y=700:
        # that is where the second row of trig keys sits, and a ten-wide row
        # there drew straight over trigs 9-16 and made them unclickable.
        for i, (label, code) in enumerate(overflow):
            x, y = 352 + (i % 5) * 112, 458 + (i // 5) * 34
            rect = self._rr(x, y, 102, 30, 6, fill=FACE, outline=EDGE)
            txt = c.create_text(x + 51, y + 15, text=label[:14], fill=TEXT,
                                font=('Helvetica', 8))
            self.items[code] = (rect, txt)
            self.text_fill[code] = TEXT
            self._bind_button(rect, code)
            self._bind_button(txt, code)

        # Key LEDs light the key itself; the four pattern-page LEDs have no
        # key and sit in a row under PAGE, page 1 on the left.
        dev = getattr(self.emu, 'device', None)
        self.led_of = {code: led for led, code in
                       (getattr(dev, 'leds', None) or {}).items()}
        page = BUTTONS['PAGE']
        for i, led in enumerate(getattr(dev, 'page_leds', ()) or ()):
            cx, cy = page[0] + 13 + i * 22, page[1] + page[3] + 12
            dot = c.create_oval(cx - 5, cy - 5, cx + 5, cy + 5,
                                fill=LED_OFF, outline=EDGE)
            self.page_items.append((led, dot))
        self._led_version = -1

        for label, code in self.enc_codes.items():
            spec = ENCODERS.get(label)
            if spec is None:
                continue
            x, y, r = spec
            ring = c.create_oval(x - r, y - r, x + r, y + r, fill='#171b21',
                                 outline='#3c444f', width=2)
            mark = c.create_line(x, y - r + 6, x, y - 4, fill=TEXT, width=3)
            # No label drawn here: the push-switch button below the knob
            # carries the firmware's own name for it, so drawing one too
            # would print it twice.
            self.enc_items[code] = [ring, mark, 0.0]
            for item in (ring, mark):
                # Tk reports the wheel differently per platform: a signed
                # delta on Windows and macOS, buttons 4 and 5 on X11. A
                # canvas tag_bind is stricter than a widget bind and REFUSES
                # <MouseWheel> outright on X11 ("requested illegal events"),
                # so it is attempted rather than assumed.
                try:
                    c.tag_bind(item, '<MouseWheel>',
                               lambda e, k=code: self.turn(
                                   k, 1 if e.delta > 0 else -1, e))
                except tk.TclError:
                    pass
                c.tag_bind(item, '<Button-4>',
                           lambda e, k=code: self.turn(k, 1, e))
                c.tag_bind(item, '<Button-5>',
                           lambda e, k=code: self.turn(k, -1, e))
                # Dragging works everywhere and needs no wheel at all.
                c.tag_bind(item, '<ButtonPress-1>',
                           lambda e, k=code: self._drag_start(k, e))
                c.tag_bind(item, '<B1-Motion>',
                           lambda e, k=code: self._drag(k, e))

    # ----------------------------------------------------------------- input
    def press(self, code, event=None):
        if event is not None and event.state & 0x0001:      # shift: latch
            if code in self.latched:
                self.latched.discard(code)
                self.held.discard(code)
                self.emu.inbox.append(('release', code, 0))
            else:
                self.latched.add(code)
                self.held.add(code)
                self.emu.inbox.append(('press', code, 0))
        else:
            self.held.add(code)
            self.emu.inbox.append(('press', code, 0))
        self._paint(code)

    def release(self, code):
        if code in self.latched:        # a latched key ignores mouse-up
            return
        self.held.discard(code)
        self.emu.inbox.append(('release', code, 0))
        self._paint(code)

    def clear_latched(self):
        for code in list(self.latched):
            self.latched.discard(code)
            self.held.discard(code)
            self.emu.inbox.append(('release', code, 0))
            self._paint(code)

    def _drag_start(self, code, event):
        self._drag_y = event.y
        self._drag_acc = 0.0

    def _drag(self, code, event):
        """Vertical drag turns an encoder: up is clockwise, 6px per detent."""
        dy = getattr(self, '_drag_y', event.y) - event.y
        self._drag_y = event.y
        self._drag_acc = getattr(self, '_drag_acc', 0.0) + dy / 6.0
        step = int(self._drag_acc)
        if step:
            self._drag_acc -= step
            self.turn(code, step)

    def turn(self, code, step, event=None):
        if event is not None and event.state & 0x0001:
            step *= 10
        self.emu.inbox.append(('encoder', code, step))
        state = self.enc_items.get(code)
        if not state:
            return
        ring, mark, angle = state
        # A Digitakt encoder has 24 detents per turn: 15 degrees each. The
        # firmware's own step per detent is velocity-scaled, so the ring is
        # a record of detents sent, not of the value.
        state[2] = angle + step * (2 * math.pi / 24)
        x0, y0, x1, y1 = self.canvas.coords(ring)
        cx, cy, r = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2
        a = state[2] - math.pi / 2
        self.canvas.coords(mark,
                           cx + math.cos(a) * (r - 22),
                           cy + math.sin(a) * (r - 22),
                           cx + math.cos(a) * (r - 6),
                           cy + math.sin(a) * (r - 6))

    def _led_rgb(self, led):
        """The LED's colour, or None when it is dark (or not defined yet)."""
        rgb = self._leds.get(led)
        if rgb is None or max(rgb) < LED_DARK:
            return None
        return rgb

    def _paint(self, code):
        item = self.items.get(code)
        if not item:
            return
        rect, txt = item
        text = self.text_fill.get(code, TEXT)
        rgb = self._led_rgb(self.led_of.get(code))
        if code in self.latched:
            self.canvas.itemconfigure(rect, fill=AMBER, outline=AMBER)
        elif code in self.held:
            self.canvas.itemconfigure(rect, fill=LIT, outline='#5b6779')
        elif rgb is not None:
            face = _mix(FACE, rgb, LED_MIX)
            self.canvas.itemconfigure(rect, fill=_hex(face),
                                      outline=_hex(rgb))
            if _luma(face) > 0.45:
                text = BG
        else:
            self.canvas.itemconfigure(rect, fill=FACE, outline=EDGE)
        self.canvas.itemconfigure(txt, fill=text)

    def _draw_leds(self):
        """Repaint whatever the firmware's LED stream changed."""
        emu = self.emu
        version = getattr(emu, 'led_version', 0)
        if version == self._led_version:
            return
        self._led_version = version
        old, self._leds = self._leds, dict(getattr(emu, 'leds', {}) or {})
        for code, led in self.led_of.items():
            if old.get(led) != self._leds.get(led):
                self._paint(code)
        for led, dot in self.page_items:
            rgb = self._led_rgb(led)
            self.canvas.itemconfigure(
                dot, fill=_hex(rgb) if rgb else LED_OFF)

    # -------------------------------------------------------------- shutdown
    def quit_all(self):
        """Stop the emulator thread, then tear the window down.

        Idempotent: it is called both from WM_DELETE_WINDOW and from the
        `finally` around mainloop, and either may run first.
        """
        player = getattr(self, 'player', None)
        if player is not None:
            player.stop()
        emu = getattr(self, 'emu', None)
        if emu is not None and emu.is_alive():
            emu.stop_flag.set()
            emu.pause.clear()
            emu.join(timeout=5)
        try:
            self.destroy()
        except tk.TclError:          # already torn down
            pass

    # ------------------------------------------------------------------ loop
    def tick(self):
        # The reschedule is in `finally` on purpose. An exception anywhere in
        # here -- a bad bind, a half-built control surface -- otherwise skips
        # the `after` and silently kills the refresh loop for good, leaving a
        # window that looks alive but never updates again.
        try:
            self._tick()
        except Exception as exc:                       # noqa: BLE001
            import traceback
            traceback.print_exc()
            self.canvas.itemconfigure(self.status,
                                      text='tick error: %s' % exc)
        finally:
            self.after(40, self.tick)

    def _tick(self):
        emu = self.emu
        if not self._named and getattr(emu, 'button_names', None):
            # MEASURED names win over the firmware's own table, and must
            # DISPLACE it: this layout places a button by its label, and the
            # panel-test table calls code 25 "PLAY" while the code that
            # actually starts the transport is 10. Letting both claim the
            # label would leave whichever lost sending a trig from the PLAY
            # button, which is the bug this fixes.
            measured = dict(getattr(getattr(emu, 'device', None),
                                    'labels', None) or {})
            taken = set(measured.values())
            names = dict(measured)
            for code, name in (emu.button_names or {}).items():
                if code in names or name in taken:
                    continue
                names[code] = name
            self.codes = {n: c for c, n in names.items()}
            self.enc_codes = {n: c for c, n in
                              getattr(emu, 'encoder_names', {}).items()}
            # Set before building, not after: if a bind fails the surface is
            # drawn once and imperfect, rather than retried every 40ms for
            # the life of the window.
            self._named = True
            self._build_controls()
        if self._named:
            self._draw_leds()
        self._draw_audio_status()
        fb = getattr(emu, 'fb', None)
        if fb:
            self.draw_screen(fb)
        held = ', '.join(sorted(self.codes and
                                [n for n, c in self.codes.items()
                                 if c in self.held] or [])) or '-'
        self.canvas.itemconfigure(
            self.status, text='held: %s      (shift-click latches, Esc clears)'
                              % held)


def main(argv):
    # --no-audio: skip the audio model, for speed or for a snapshot whose
    # audio DMA is not set up (the window says so either way).
    audio = '--no-audio' not in argv
    argv = [a for a in argv if a != '--no-audio']
    syx = argv[1] if len(argv) > 1 else None
    # The panel is for using the instrument, so its +Drive persists: whatever
    # the firmware writes to the card lands in plusdrive.img next to the
    # firmware, unless DT2_PLUSDRIVE already says otherwise.
    os.environ.setdefault('DT2_PLUSDRIVE', 'plusdrive.img')
    if argv:
        snap = argv[0]
    else:
        # Only the tested build keeps the historic top-level snapshot path;
        # every other firmware gets its own directory, so ask run.paths_for
        # rather than assuming the flat name and failing on a product whose
        # snapshots are one level down.
        from emu import run as _run
        snap, _prefix = _run.paths_for(config.firmware(syx))
    app = DigitaktPanel(snap, syx=syx, audio=audio)
    try:
        app.mainloop()
    finally:
        # Ctrl-C, the X server going away, or an exception out of mainloop
        # all bypass WM_DELETE_WINDOW and would leave the worker inside
        # Unicorn while the interpreter frees it.
        app.quit_all()


if __name__ == '__main__':
    main(sys.argv[1:])
