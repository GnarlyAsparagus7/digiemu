"""dt2/elz.py against fixed packed streams and, when present, 1.15C's sections.

The fixtures were produced by this repository's own packer (dt2/aplib.py;
tests/test_aplib.py checks they still match it where the packer is present).
They are not firmware bytes: each is the packed form of the data beside it.
"""

import hashlib
import os
import unittest

from dt2 import elz
from dt2.container import sections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYX = os.path.join(ROOT, 'Digitakt_II_OS1.15C.syx')
SECTIONS = os.path.join(ROOT, 'sections')

# name -> (data, packed with matches?, packed stream as hex)
FIXTURES = {
    'store only': (
        b'hello world', False,
        '00000014000007caff68656c6c6f20776fe0726c64000000000090ff'),
    'two-byte cycle': (
        b'ab' * 4000, True,
        '00000013000004a5d961620155551b012a0a8b00000000000480ff'),
    # A repeat straddling the near/far offset boundary at 3328, past which the
    # decoder adds one to the length.
    'far repeat': (
        (b'x' * 4000) + (b'ab' * 100) + (b'x' * 4000), True,
        '0000001a0000084bb27800a82a1d6162900105b0e1ad9500400580000000000240ff'),
    'block repeat': (
        bytes(range(64)) * 80, True,
        '00000058000014faff0001020304050607ff08090a0b0c0d0e0fff10111213141516'
        '17ff18191a1b1c1d1e1fff2021222324252627ff28292a2b2c2d2e2fff3031323334'
        '353637ff38393a3b3c3d3e3f653f55546c3fa2a8c0000000000120ff'),
}


def sections_from_syx():
    """True when sections/ was extracted from the 1.15C .syx in the repo root."""
    marker = os.path.join(SECTIONS, '.source-sha256')
    if not (os.path.exists(SYX) and os.path.exists(marker)):
        return False
    with open(SYX, 'rb') as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    with open(marker) as f:
        return f.read().strip() == digest


class ElzTest(unittest.TestCase):
    def test_fixtures_depack(self):
        for name, (data, _compress, packed) in FIXTURES.items():
            self.assertEqual(elz.depack_section(bytes.fromhex(packed)), data, name)

    def test_declared_length_must_match_the_end_marker(self):
        packed = bytearray.fromhex(FIXTURES['store only'][2])
        packed[3] += 4  # declare four more stream bytes than the end marker uses
        with self.assertRaises(ValueError):
            elz.depack_section(bytes(packed) + bytes(4))

    @unittest.skipUnless(sections_from_syx(), 'needs the 1.15C .syx and sections/ extracted from it')
    def test_matches_the_device_depacker_on_1_15c(self):
        c, secs = sections(SYX)
        names = {2: 'section_2_DSP.bin', 3: 'section_3_MAIN_OS.bin', 7: 'section_7_BLOB.bin'}
        for sid, off, clen, dest in secs:
            if sid in names:
                with self.subTest(section=sid):
                    with open(os.path.join(SECTIONS, names[sid]), 'rb') as f:
                        expected = f.read()
                    self.assertEqual(elz.depack_section(bytes(c[off:off + clen])), expected)


if __name__ == '__main__':
    unittest.main()
