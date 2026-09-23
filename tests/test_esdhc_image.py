"""The eMMC image file: writes land in it at flush, reads come back after reopen."""
import os
import tempfile
import unittest

from emu.esdhc import Card


class ImageFileTest(unittest.TestCase):
    def test_round_trip_and_sparse_growth(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'plusdrive.img')
            c = Card(path=p)
            self.assertEqual(len(c.image), 512)          # created, one sector
            payload = bytes(range(256)) * 2
            c.write_data(25, 0x1000, payload)            # sector 0x1000 = byte 0x200000
            c.write_data(25, 0, b'\xbe\xef\xba\xce' + b'\0' * 508)
            c.flush()
            self.assertEqual(os.path.getsize(p), 0x200000 + 512)
            c2 = Card(path=p)
            self.assertEqual(c2.data_for(18, 0x1000, 512), payload)
            self.assertEqual(bytes(c2.data_for(18, 0, 4)), b'\xbe\xef\xba\xce')
            self.assertEqual(c2.data_for(18, 7, 512), b'\0' * 512)   # unwritten reads zero

    def test_no_path_is_in_memory(self):
        c = Card()
        c.write_data(25, 3, b'x' * 512)
        c.flush()                                        # no-op, no error
        self.assertEqual(c.data_for(18, 3, 512), b'x' * 512)


if __name__ == '__main__':
    unittest.main()
