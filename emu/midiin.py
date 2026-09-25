"""Live MIDI controller bridge for digiemu front panels.

Listens for incoming MIDI messages from connected hardware controllers
(such as an Elektron Digitakt or generic MIDI controller/keyboard) and
translates them into front-panel button presses, releases, and encoder turns.
"""
import logging
import os
import threading
import time

try:
    import mido
except ImportError:
    mido = None

LOG = logging.getLogger(__name__)

# Standard Elektron Digitakt chromatic keyboard note map:
# White keys row (bottom, trigs 9-16) and black keys row (top, trigs 1-8).
# Keyed by note index within an octave (0 = C, 1 = C#, etc.).
CHROMATIC_OCTAVE = {
    0: '9',    # C  (Trig 9)
    1: '1',    # C# (Trig 1)
    2: '10',   # D  (Trig 10)
    3: '2',    # D# (Trig 2)
    4: '11',   # E  (Trig 11)
    5: '12',   # F  (Trig 12)
    6: '4',    # F# (Trig 4)
    7: '13',   # G  (Trig 13)
    8: '5',    # G# (Trig 5)
    9: '14',   # A  (Trig 14)
    10: '6',   # A# (Trig 6)
    11: '15',  # B  (Trig 15)
}

# Linear mappings for 16 pads/keys (e.g. C1-D#2 or C2-D#3) -> Trigs 1-16:
LINEAR_36 = {36 + i: str(i + 1) for i in range(16)}
LINEAR_48 = {48 + i: str(i + 1) for i in range(16)}
LINEAR_60 = {60 + i: str(i + 1) for i in range(16)}

DEFAULT_NOTE_LAYOUT = 'default'
NOTE_LAYOUTS = {
    DEFAULT_NOTE_LAYOUT: None,
    'linear_36': LINEAR_36,
    'linear_48': LINEAR_48,
    'linear_60': LINEAR_60,
}

CC_PAGE_MAPS = {
    'src': {
        16: 'A', 17: 'B', 18: 'C', 19: 'D',
        20: 'E', 21: 'F', 22: 'G', 23: 'H',
    },
    'filter': {
        74: 'A', 75: 'B', 76: 'C', 77: 'D',
        78: 'E', 79: 'F', 82: 'G', 83: 'H',
    },
    'amp': {
        104: 'A', 105: 'B', 106: 'C', 107: 'D',
        10: 'E', 95: 'F', 12: 'G', 13: 'H',
    },
    'lfo': {
        108: 'A', 109: 'B', 110: 'C', 111: 'D',
        112: 'E', 113: 'F', 114: 'G', 115: 'H',
    },
    'midi': {
        70: 'A', 71: 'B', 72: 'C', 73: 'D',
        74: 'E', 75: 'F', 76: 'G', 77: 'H',
    },
    'level': {
        7: 'LEVEL/DATA', 95: 'LEVEL/DATA',
    },
}
DEFAULT_CC_PAGE = 'filter'
CC_PAGE_ALIASES = {'standard': DEFAULT_CC_PAGE}
CC_PAGE_ENV = 'DIGIEMU_MIDI_CC_PAGE'
NOTE_LAYOUT_ENV = 'DIGIEMU_MIDI_NOTE_LAYOUT'
ENCODER_CC_MAP = dict(CC_PAGE_MAPS[DEFAULT_CC_PAGE])


def _normalise_name(value):
    if not isinstance(value, str):
        return value
    return value.strip().lower().replace('-', '_').replace(' ', '_')


def _select_cc_page(page):
    selected = _normalise_name(page)
    selected = CC_PAGE_ALIASES.get(selected, selected)
    if selected not in CC_PAGE_MAPS:
        raise ValueError('unknown MIDI CC page: %s' % page)
    return selected


def _select_note_layout(layout):
    selected = _normalise_name(layout)
    if selected not in NOTE_LAYOUTS:
        raise ValueError('unknown MIDI note layout: %s' % layout)
    return selected


def find_midi_input():
    """Find the best available MIDI input port name, prioritizing Elektron devices."""
    if not mido:
        return None
    try:
        ports = mido.get_input_names()
    except Exception:
        return None
    if not ports:
        return None

    # Check override
    override = os.environ.get('DIGIEMU_MIDI_IN')
    if override:
        for p in ports:
            if override.lower() in p.lower():
                return p

    # Prefer Elektron devices (Digitakt, Digitone, etc.)
    for p in ports:
        lower = p.lower()
        if 'digitakt' in lower or 'digitone' in lower or 'elektron' in lower:
            return p

    # Fallback to the first available port
    return ports[0]


class MidiBridge:
    """Bridges incoming MIDI messages to an active panel window."""

    def __init__(self, panel, port_name=None, cc_page=None, note_layout=None,
                 page=None):
        if cc_page is not None and page is not None:
            if _select_cc_page(cc_page) != _select_cc_page(page):
                raise ValueError('conflicting MIDI CC pages')
        self.panel = panel
        self.port_name = port_name or find_midi_input()
        requested_page = cc_page if cc_page is not None else page
        if requested_page is None:
            requested_page = (os.environ.get(CC_PAGE_ENV)
                              or os.environ.get('DIGIEMU_MIDI_PAGE'))
        if requested_page is None:
            self.cc_page = DEFAULT_CC_PAGE
        else:
            try:
                self.cc_page = _select_cc_page(requested_page)
            except ValueError:
                if cc_page is not None or page is not None:
                    raise
                LOG.warning('unknown %s=%r; using %s', CC_PAGE_ENV,
                            requested_page, DEFAULT_CC_PAGE)
                self.cc_page = DEFAULT_CC_PAGE
        self.encoder_cc_map = dict(CC_PAGE_MAPS[self.cc_page])
        requested_layout = note_layout
        if requested_layout is None:
            requested_layout = os.environ.get(NOTE_LAYOUT_ENV)
        if requested_layout is None:
            self.note_layout = DEFAULT_NOTE_LAYOUT
        else:
            try:
                self.note_layout = _select_note_layout(requested_layout)
            except ValueError:
                if note_layout is not None:
                    raise
                LOG.warning('unknown %s=%r; using %s', NOTE_LAYOUT_ENV,
                            requested_layout, DEFAULT_NOTE_LAYOUT)
                self.note_layout = DEFAULT_NOTE_LAYOUT
        self.note_map = NOTE_LAYOUTS[self.note_layout]
        self._thread = None
        self._stop_event = threading.Event()
        self._last_cc = {}
        self._active_notes = {}
        self._note_sources = {}

    def start(self):
        """Start the MIDI listener thread."""
        if not mido or not self.port_name:
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name='digiemu-midi-in')
        self._thread.start()
        print(f"[midi] Connected to {self.port_name}", flush=True)
        return True

    def stop(self):
        """Stop listening and close the MIDI port."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.5)
        if thread is self._thread and (thread is None or not thread.is_alive()):
            self._thread = None
        for code in list(self._note_sources):
            try:
                self.panel.after(0, lambda c=code: self.panel.release(c))
            except Exception:
                pass
        self._active_notes.clear()
        self._note_sources.clear()

    def _resolve_note(self, msg):
        """Map a Note On/Off message to a panel button code."""
        if self.note_layout != DEFAULT_NOTE_LAYOUT:
            return self.note_map.get(msg.note)

        # 1. Linear pad ranges (e.g. 4x4 pads on notes 36..51)
        if msg.note in LINEAR_36:
            return LINEAR_36[msg.note]

        # 2. Chromatic keyboard layout (relative to octave C)
        semi = msg.note % 12
        # Upper octave extension on DT keyboard: high C (12), high C# (13), high D# (15)
        if msg.note in (72, 84) and semi == 0:
            return '16'
        if msg.note in (73, 85) and semi == 1:
            return '7'
        if msg.note in (75, 87) and semi == 3:
            return '8'

        if semi in CHROMATIC_OCTAVE:
            return CHROMATIC_OCTAVE[semi]

        # 3. Channel-based track selection (channels 0..7 -> trigs 1..8)
        channel = getattr(msg, 'channel', None)
        if isinstance(channel, int) and 0 <= channel <= 7:
            return str(channel + 1)

        return None

    def _press_and_release(self, code, duration=0.12):
        """Momentarily press and release a transport button."""
        try:
            self.panel.after(0, lambda: self.panel.press(code))
            self.panel.after(int(duration * 1000), lambda: self.panel.release(code))
        except Exception:
            pass

    def _dispatch(self, msg):
        """Process a single incoming MIDI message."""
        mtype = msg.type

        # Clock messages are ignored to keep the loop efficient
        if mtype == 'clock':
            return

        print(f"[midi] rx: {msg}", flush=True)

        # Transport
        if mtype in ('start', 'continue'):
            print("[midi] -> PLAY", flush=True)
            self._press_and_release('PLAY')
            return
        if mtype == 'stop':
            print("[midi] -> STOP", flush=True)
            self._press_and_release('STOP')
            return

        # Note On
        if mtype == 'note_on' and msg.velocity > 0:
            code = self._resolve_note(msg)
            print(f"[midi] -> NoteOn note={msg.note} trig={code}", flush=True)
            if code and code in self.panel.BUTTONS:
                if msg.note in self._active_notes:
                    return
                self._active_notes[msg.note] = code
                sources = self._note_sources.setdefault(code, set())
                if not sources:
                    try:
                        self.panel.after(0, lambda c=code: self.panel.press(c))
                    except Exception:
                        pass
                sources.add(msg.note)
            return

        # Note Off
        if mtype == 'note_off' or (mtype == 'note_on' and msg.velocity == 0):
            code = self._active_notes.pop(msg.note, None) or self._resolve_note(msg)
            if code and code in self.panel.BUTTONS:
                sources = self._note_sources.get(code)
                if sources is not None:
                    sources.discard(msg.note)
                    if sources:
                        return
                    self._note_sources.pop(code, None)
                try:
                    self.panel.after(0, lambda c=code: self.panel.release(c))
                except Exception:
                    pass
            return

        # Control Change (Encoders)
        if mtype == 'control_change':
            enc = self.encoder_cc_map.get(msg.control)
            if not enc:
                return
            last = self._last_cc.get(msg.control)
            self._last_cc[msg.control] = msg.value
            if last is None:
                return

            delta = msg.value - last
            # Suppress large jumps from preset/page switches
            if 0 < abs(delta) < 32:
                print(f"[midi] -> Knob {enc} delta={delta}", flush=True)
                try:
                    # Encoders in dtpanel scale step; 1 unit per CC tick
                    self.panel.after(0, lambda e=enc, d=delta: self.panel.turn(e, d))
                except Exception:
                    pass
            return

    def _run(self):
        """Background listening loop."""
        try:
            with mido.open_input(self.port_name) as inport:
                while not self._stop_event.is_set():
                    for msg in inport.iter_pending():
                        self._dispatch(msg)
                    time.sleep(0.005)
        except Exception as exc:
            print(f"[midi] MIDI listener stopped: {exc}", flush=True)
