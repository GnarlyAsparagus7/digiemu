"""emu/samples.py: WAV files onto a +Drive image's /incoming, in two halves.

plan() must turn a bad file away before the panel stops anything, pick names
the firmware will not confuse with one already there, and never write;
write() must add samples the firmware resolves and re-check names against
the card as it is by then. All on small synthetic images (ekfsformat's own
format at base 0); every WAV is made up here.
"""
import os
import struct
import tempfile
import unittest
from unittest import mock

from emu import ekfsformat as ek
from emu import samples


def _wav(n=4, rate=48000, chans=1, bits=16):
    width = bits // 8
    body = bytes(n * chans * width)
    fmt = struct.pack('<HHIIHH', 1, chans, rate, rate * chans * width,
                      chans * width, bits)
    riff = b'WAVE' + b'fmt ' + struct.pack('<I', len(fmt)) + fmt
    riff += b'data' + struct.pack('<I', len(body)) + body
    return b'RIFF' + struct.pack('<I', len(riff)) + riff


class SamplesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.card = os.path.join(self.tmp, 'plusdrive.img')
        ek.format_image(self.card, base=0)

    def file(self, name, data=None, sub=''):
        d = os.path.join(self.tmp, sub)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name)
        with open(path, 'wb') as fh:
            fh.write(_wav() if data is None else data)
        return path

    def plan(self, files):
        return samples.plan(files, self.card, base=0)

    def names_on_card(self):
        fs = ek.Ekfs(self.card, 0)
        try:
            inc = ek.find_dir(fs, samples.INCOMING)
            return [n.decode('latin-1') for _l, _i, n, _t in
                    fs.dir_entries(inc) if n not in (b'.', b'..')]
        finally:
            fs.close()

    def put(self, name):
        """A sample already on the card, the way ekfsadd puts one there."""
        fs = ek.Ekfs(self.card, 0, write=True)
        try:
            fs.add_sample(ek.find_dir(fs, samples.INCOMING), name, _wav())
        finally:
            fs.close()

    # -- plan ------------------------------------------------------------
    def test_names_rates_and_sizes(self):
        a = self.file('Kick 01.wav', _wav(n=44100, rate=44100))
        b = self.file('snare.WAV', _wav(n=10, chans=2, bits=24))
        before = os.path.getmtime(self.card)
        p = self.plan([a, b])
        self.assertEqual(p.rejected, [])
        self.assertEqual([(s.name, s.rate, s.frames, s.renamed)
                          for s in p.samples],
                         [('Kick 01', 44100, 44100, False),
                          ('snare', 48000, 10, False)])
        self.assertAlmostEqual(p.samples[0].seconds, 1.0)
        # 0x40 header + 88200 bytes of PCM + 0x10 trailer: six 16 KB blocks
        self.assertEqual([s.blocks for s in p.samples], [6, 1])
        self.assertEqual(os.path.getmtime(self.card), before)   # read only
        self.assertEqual(self.names_on_card(), [])

    def test_what_cannot_go_on_is_named_and_left_out(self):
        good = self.file('ok.wav')
        text = self.file('notes.wav', b'not a wave file at all')
        odd = self.file('odd.wav', _wav(bits=12))
        empty = self.file('empty.wav', _wav(n=0))
        gone = os.path.join(self.tmp, 'gone.wav')
        p = self.plan([text, good, odd, empty, gone])
        self.assertEqual([s.name for s in p.samples], ['ok'])
        why = {os.path.basename(path): reason for path, reason in p.rejected}
        self.assertEqual(sorted(why), ['empty.wav', 'gone.wav', 'notes.wav',
                                       'odd.wav'])
        self.assertIn('RIFF/WAVE', why['notes.wav'])
        self.assertIn('12-bit', why['odd.wav'])
        self.assertEqual(why['empty.wav'], 'no audio in it')

    def test_a_name_the_firmware_would_confuse_gets_a_number(self):
        self.put('kick.wav')
        self.put('tom1a.wav')
        files = [self.file('KICK.wav'),              # case-insensitive
                 self.file('tom1b.wav'),             # the skipped character
                 self.file('hat.wav', sub='a'),      # the same name twice
                 self.file('hat.wav', sub='b')]
        p = self.plan(files)
        self.assertEqual([(s.name, s.renamed) for s in p.samples],
                         [('KICK-2', True), ('tom1b-2', True),
                          ('hat', False), ('hat-2', True)])

    def test_card_names_are_printable_ascii_and_bounded(self):
        self.assertEqual(samples.card_name('/x/Kïck ✓.wav'),
                         'K_ck _')
        self.assertEqual(samples.card_name('/x/  pad  .wav'), 'pad')
        self.assertEqual(samples.card_name('/x/ .wav'), 'sample')
        long = samples.card_name('/x/' + 'a' * 100 + '.wav')
        self.assertEqual(long, 'a' * samples.NAME_MAX)
        name, renamed = samples._free_name(long, [long.encode()])
        self.assertEqual((len(name), name[-2:], renamed),
                         (samples.NAME_MAX, '-2', True))

    def test_a_full_folder_or_card_refuses_the_rest(self):
        files = [self.file('%d.wav' % i) for i in range(3)]
        # /incoming holds '.' and '..'; room for one more entry.
        with mock.patch.object(ek, 'MAX_DIR_ENTRIES', 3):
            p = self.plan(files)
        self.assertEqual([s.name for s in p.samples], ['0'])
        self.assertEqual([r for _p, r in p.rejected],
                         ['the /incoming folder is full'] * 2)
        with mock.patch.object(ek.Ekfs, 'used',
                               lambda fs, which: fs.block_count
                               if which == 'block' else 0):
            p = self.plan(files[:1])
        self.assertEqual([r for _p, r in p.rejected],
                         ['not enough room left on the +Drive'])

    def test_a_card_that_cannot_take_samples_is_an_error(self):
        wav = self.file('a.wav')
        with self.assertRaises(samples.Error):
            samples.plan([wav], os.path.join(self.tmp, 'missing.img'), base=0)
        blank = os.path.join(self.tmp, 'blank.img')
        with open(blank, 'wb') as fh:
            fh.write(bytes(4096))
        with self.assertRaisesRegex(samples.Error, 'no ekFS'):
            samples.plan([wav], blank, base=0)
        bare = os.path.join(self.tmp, 'bare.img')
        ek.format_image(bare, base=0, with_incoming=False)
        with self.assertRaisesRegex(samples.Error, 'no /incoming'):
            samples.plan([wav], bare, base=0)

    # -- write -----------------------------------------------------------
    def test_written_samples_resolve_like_firmware_files(self):
        data = _wav(n=100, rate=44100)
        p = self.plan([self.file('Bell.wav', data), self.file('b.wav')])
        seen = []
        written = samples.write(p, progress=lambda s: seen.append(s.name))
        self.assertEqual(seen, ['Bell', 'b'])
        self.assertEqual([n for n, _i in written], ['Bell', 'b'])
        self.assertEqual(self.names_on_card(), ['Bell', 'b'])
        fs = ek.Ekfs(self.card, 0)
        try:
            self.assertTrue(fs.checksum_ok())
            ino = written[0][1]
            content, rate, _f = ek.wav_to_sample(data)
            self.assertEqual(struct.unpack('>I', fs.inode(ino)[0x08:0x0C])[0],
                             ek.find_dir(fs, samples.INCOMING))
            self.assertEqual(struct.unpack('>I', fs.inode(ino)[0x0C:0x10])[0],
                             ek.ekfs_hash(content, ek.SAMPLE_HASH_SEED) | 1)
            self.assertEqual(rate, 44100)
        finally:
            fs.close()

    def test_write_checks_names_again(self):
        p = self.plan([self.file('snare.wav')])
        self.put('Snare.wav')           # arrived after the plan read the card
        samples.write(p)
        self.assertEqual((p.samples[0].name, p.samples[0].renamed),
                         ('snare-2', True))
        self.assertEqual(self.names_on_card(), ['Snare', 'snare-2'])

    def test_a_failure_part_way_keeps_what_was_written(self):
        p = self.plan([self.file('one.wav'), self.file('two.wav')])
        os.remove(p.samples[1].path)
        with self.assertRaises(OSError):
            samples.write(p)
        self.assertEqual([n for n, _i in p.written], ['one'])
        self.assertEqual(self.names_on_card(), ['one'])


class WavInfoTest(unittest.TestCase):
    """wav_info makes wav_to_sample's checks without converting."""

    def test_agrees_with_the_conversion(self):
        info = ek.wav_info(_wav(n=7, rate=22050, chans=2, bits=24))
        self.assertEqual((info.tag, info.channels, info.rate, info.bits,
                          info.frames), (1, 2, 22050, 24, 7))
        _c, rate, frames = ek.wav_to_sample(_wav(n=7, rate=22050, chans=2,
                                                 bits=24))
        self.assertEqual((rate, frames), (22050, 7))

    def test_a_short_fmt_chunk_is_an_error_not_a_crash(self):
        fmt = struct.pack('<HHI', 1, 1, 48000)
        riff = b'WAVE' + b'fmt ' + struct.pack('<I', len(fmt)) + fmt
        riff += b'data' + struct.pack('<I', 2) + b'\x00\x00'
        with self.assertRaisesRegex(ek.Error, 'fmt chunk'):
            ek.wav_info(b'RIFF' + struct.pack('<I', len(riff)) + riff)

    def test_too_long_for_the_loader(self):
        with mock.patch.object(ek, 'MAX_SAMPLE_PCM', 6):
            ek.wav_info(_wav(n=3))
            with self.assertRaisesRegex(ek.Error, 'at most 6'):
                ek.wav_info(_wav(n=4))


if __name__ == '__main__':
    unittest.main()
