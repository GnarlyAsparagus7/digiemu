import os
import struct
import sys
import tempfile
import time
import unittest
import wave

from emu import audioout


class AudioOutTest(unittest.TestCase):
    def test_frames_from_ssi(self):
        # 32-bit word, 24-bit sample: bytes 1 and 2 of each 4-byte word are 16-bit LE
        raw = bytes([0x00, 0x12, 0x34, 0x00, 0x00, 0x56, 0x78, 0x00])
        pcm = audioout.frames_from_ssi(raw, word_bits=32, sample_bits=24)
        self.assertEqual(len(pcm), 4)
        # first word: data[2]=0x34, data[1]=0x12 -> 0x34, 0x12
        # second word: data[6]=0x78, data[5]=0x56 -> 0x78, 0x56
        self.assertEqual(pcm, bytes([0x34, 0x12, 0x78, 0x56]))

    def test_trim_silence(self):
        silence = bytes(400)
        sound = struct.pack('<hh', 500, 500) * 100
        pcm = silence + sound + silence
        trimmed = audioout.trim_silence(pcm, channels=2, threshold=10, rate=48000, pad_ms=2)
        self.assertTrue(len(trimmed) > len(sound))
        self.assertTrue(len(trimmed) < len(pcm))

    def test_wav_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'test.wav')
            w = audioout.WavFile(path, rate=48000, channels=2)
            data = struct.pack('<hh', 100, 200) * 100
            w.write(data)
            self.assertEqual(w.queued(), 0)
            self.assertEqual(w.played, 1)
            w.close()

            with wave.open(path, 'rb') as r:
                self.assertEqual(r.getnchannels(), 2)
                self.assertEqual(r.getsampwidth(), 2)
                self.assertEqual(r.getframerate(), 48000)
                self.assertEqual(r.getnframes(), 100)

    @unittest.skipUnless(sys.platform in ('win32', 'darwin'), 'WaveOut supports Windows and macOS')
    def test_waveout_lifecycle(self):
        out = audioout.WaveOut(rate=48000, channels=2, buffers=8, block_ms=10)
        self.assertEqual(out.rate, 48000)
        self.assertEqual(out.channels, 2)
        self.assertEqual(out.queued(), 0)

        # Write two blocks
        pcm = struct.pack('<hh', 100, 100) * (out.block // 4) * 2
        out.write(pcm, block=True)
        self.assertTrue(out.played > 0)
        out.drain()
        self.assertEqual(out.queued(), 0)
        out.close()

    @unittest.skipUnless(sys.platform in ('win32', 'darwin'), 'Player supports Windows and macOS')
    def test_player_lifecycle(self):
        player = audioout.Player(rate=48000, channels=2)
        self.assertIsNone(player.error)
        self.assertFalse(player.playing)

        pcm = struct.pack('<hh', 100, 100) * 960  # 20ms
        player.play(pcm)
        time.sleep(0.01)
        player.stop()
        self.assertFalse(player.playing)
        self.assertIsNone(player.error)


if __name__ == '__main__':
    unittest.main()
