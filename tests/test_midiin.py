"""Tests for emu.midiin: live MIDI controller bridge."""
import unittest
from types import SimpleNamespace
from unittest import mock

from emu import midiin


class DummyPanel:
    def __init__(self):
        self.BUTTONS = {str(i + 1): True for i in range(16)}
        self.BUTTONS['PLAY'] = True
        self.BUTTONS['STOP'] = True
        self.BUTTONS['RECORD'] = True
        self.calls = []

    def after(self, delay_ms, func):
        # Execute immediately for testing
        func()

    def press(self, code):
        self.calls.append(('press', code))

    def release(self, code):
        self.calls.append(('release', code))

    def turn(self, code, delta):
        self.calls.append(('turn', code, delta))


class MidiInTest(unittest.TestCase):
    def setUp(self):
        self.panel = DummyPanel()
        self.bridge = midiin.MidiBridge(self.panel, port_name=None)

    def test_transport_start_and_stop(self):
        msg_start = SimpleNamespace(type='start')
        self.bridge._dispatch(msg_start)
        self.assertIn(('press', 'PLAY'), self.panel.calls)
        self.assertIn(('release', 'PLAY'), self.panel.calls)

        self.panel.calls.clear()
        msg_stop = SimpleNamespace(type='stop')
        self.bridge._dispatch(msg_stop)
        self.assertIn(('press', 'STOP'), self.panel.calls)
        self.assertIn(('release', 'STOP'), self.panel.calls)

    def test_note_on_and_off_linear_pads(self):
        # Note 36 (C1) -> Trig 1
        msg_on = SimpleNamespace(type='note_on', note=36, velocity=100, channel=0)
        self.bridge._dispatch(msg_on)
        self.assertEqual(self.panel.calls, [('press', '1')])

        self.panel.calls.clear()
        msg_off = SimpleNamespace(type='note_off', note=36, velocity=0, channel=0)
        self.bridge._dispatch(msg_off)
        self.assertEqual(self.panel.calls, [('release', '1')])

    def test_shared_trig_releases_after_last_note(self):
        note_a = SimpleNamespace(type='note_on', note=36, velocity=100)
        note_b = SimpleNamespace(type='note_on', note=61, velocity=100)
        off_a = SimpleNamespace(type='note_off', note=36, velocity=0)
        off_b = SimpleNamespace(type='note_off', note=61, velocity=0)

        self.bridge._dispatch(note_a)
        self.bridge._dispatch(note_b)
        self.assertEqual(self.panel.calls, [('press', '1')])
        self.bridge._dispatch(off_a)
        self.assertEqual(self.panel.calls, [('press', '1')])
        self.bridge._dispatch(off_b)
        self.assertEqual(self.panel.calls, [('press', '1'), ('release', '1')])

    def test_stop_releases_active_notes(self):
        self.bridge._dispatch(SimpleNamespace(
            type='note_on', note=36, velocity=100))
        self.bridge.stop()
        self.assertEqual(self.panel.calls, [('press', '1'), ('release', '1')])
        self.assertEqual(self.bridge._active_notes, {})
        self.assertEqual(self.bridge._note_sources, {})

    def test_note_on_and_off_chromatic(self):
        # Note 61 (C#) -> Trig 1 (black key C#)
        msg_on = SimpleNamespace(type='note_on', note=61, velocity=100, channel=0)
        self.bridge._dispatch(msg_on)
        self.assertEqual(self.panel.calls, [('press', '1')])

        # Note 62 (D -> semi 2 -> Trig 10)
        self.panel.calls.clear()
        msg_on_d = SimpleNamespace(type='note_on', note=62, velocity=100, channel=0)
        self.bridge._dispatch(msg_on_d)
        self.assertEqual(self.panel.calls, [('press', '10')])

        # Note 73 (High C# -> Trig 7)
        self.panel.calls.clear()
        msg_on_high = SimpleNamespace(type='note_on', note=73, velocity=100, channel=0)
        self.bridge._dispatch(msg_on_high)
        self.assertEqual(self.panel.calls, [('press', '7')])

    def test_encoder_cc_delta(self):
        # CC 74 (Filter Cutoff -> Knob A)
        msg1 = SimpleNamespace(type='control_change', control=74, value=64)
        self.bridge._dispatch(msg1)
        self.assertEqual(self.panel.calls, [])  # first message establishes baseline

        # Turn clockwise by 2
        msg2 = SimpleNamespace(type='control_change', control=74, value=66)
        self.bridge._dispatch(msg2)
        self.assertEqual(self.panel.calls, [('turn', 'A', 2)])

        # Turn counter-clockwise by 1
        self.panel.calls.clear()
        msg3 = SimpleNamespace(type='control_change', control=74, value=65)
        self.bridge._dispatch(msg3)
        self.assertEqual(self.panel.calls, [('turn', 'A', -1)])

    def test_encoder_cc_ignores_large_jumps(self):
        msg1 = SimpleNamespace(type='control_change', control=74, value=10)
        self.bridge._dispatch(msg1)
        # Large jump (e.g. preset/page change from 10 to 80)
        msg2 = SimpleNamespace(type='control_change', control=74, value=80)
        self.bridge._dispatch(msg2)
        self.assertEqual(self.panel.calls, [])

    def test_cc_pages_do_not_share_conflicting_assignments(self):
        filter_bridge = midiin.MidiBridge(self.panel, port_name=None,
                                          cc_page='filter')
        midi_bridge = midiin.MidiBridge(self.panel, port_name=None,
                                        cc_page='midi')
        self.assertEqual(filter_bridge.encoder_cc_map[76], 'C')
        self.assertEqual(midi_bridge.encoder_cc_map[76], 'G')
        with self.assertRaises(ValueError):
            midiin.MidiBridge(self.panel, port_name=None, cc_page='unknown')

    def test_note_layout_is_explicit(self):
        bridge = midiin.MidiBridge(self.panel, port_name=None,
                                   note_layout='linear_48')
        note = SimpleNamespace(type='note_on', note=48, velocity=100, channel=0)
        bridge._dispatch(note)
        self.assertEqual(self.panel.calls, [('press', '1')])
        self.panel.calls.clear()
        note = SimpleNamespace(type='note_on', note=60, velocity=100, channel=0)
        bridge._dispatch(note)
        self.assertEqual(self.panel.calls, [('press', '13')])

    def test_unknown_environment_layout_falls_back_to_default(self):
        with mock.patch.dict('os.environ', {
                midiin.CC_PAGE_ENV: 'unknown',
                midiin.NOTE_LAYOUT_ENV: 'unknown'}):
            bridge = midiin.MidiBridge(self.panel, port_name=None)
        self.assertEqual(bridge.cc_page, midiin.DEFAULT_CC_PAGE)
        self.assertEqual(bridge.note_layout, midiin.DEFAULT_NOTE_LAYOUT)


if __name__ == '__main__':
    unittest.main()
