"""eMMC ERASE (CMD35/36/38), which FORMAT +DRIVE depends on.

The firmware's FORMAT +DRIVE issues 515 (ERASE_GROUP_START, ERASE_GROUP_END,
ERASE) triples covering about 1.5 GB and only two small writes; before this
was modelled the commands got a valid response and nothing was cleared, so a
format left every byte of a persisted +Drive image in place.
"""
import os
import tempfile
import unittest

from emu.esdhc import Card


def erase(card, first_sector, last_sector):
    """Issue the three-command sequence exactly as the firmware does."""
    card.command(35, first_sector)
    card.command(36, last_sector)      # INCLUSIVE end sector
    card.command(38, 1)


class EraseTest(unittest.TestCase):
    def test_erase_clears_written_data(self):
        card = Card()
        card.write_data(25, 4096, b'\xAA' * 512)
        self.assertEqual(card.data_for(18, 4096, 512), b'\xAA' * 512)
        erase(card, 4096, 4096)
        self.assertEqual(card.data_for(18, 4096, 512), b'\x00' * 512)

    def test_erase_end_sector_is_inclusive(self):
        card = Card()
        for sec in (10, 11, 12):
            card.write_data(25, sec, b'\x5A' * 512)
        erase(card, 10, 11)
        self.assertEqual(card.data_for(18, 10, 512), b'\x00' * 512)
        self.assertEqual(card.data_for(18, 11, 512), b'\x00' * 512)
        self.assertEqual(card.data_for(18, 12, 512), b'\x5A' * 512)

    def test_write_after_erase_wins(self):
        card = Card()
        erase(card, 20, 20)
        card.write_data(25, 20, b'\x77' * 512)
        self.assertEqual(card.data_for(18, 20, 512), b'\x77' * 512)

    def test_erase_hides_bytes_already_in_the_image_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'plusdrive.img')
            with open(path, 'wb') as f:
                f.write(b'\xEE' * 4096)
            card = Card(path=path)
            self.assertEqual(card.data_for(18, 0, 512), b'\xEE' * 512)
            erase(card, 0, 1)
            self.assertEqual(card.data_for(18, 0, 512), b'\x00' * 512)
            self.assertEqual(card.data_for(18, 2, 512), b'\xEE' * 512)
            card.flush()
            with open(path, 'rb') as f:
                on_disk = f.read()
            self.assertEqual(on_disk[:1024], b'\x00' * 1024)
            self.assertEqual(on_disk[1024:2048], b'\xEE' * 1024)

    def test_huge_erase_past_end_of_file_does_not_grow_it(self):
        # FORMAT's last range runs to sector 3816527, ~1.8 GB. Beyond the
        # file's end the image is sparse and already reads zero.
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'plusdrive.img')
            card = Card(path=path)
            erase(card, 1835008, 3816527)
            card.flush()
            self.assertLess(os.path.getsize(path), 1 << 20)
            self.assertEqual(card.data_for(18, 1835008, 512), b'\x00' * 512)

    def test_erase_ranges_merge(self):
        card = Card()
        erase(card, 0, 0)
        erase(card, 1, 1)
        erase(card, 2, 2)
        self.assertEqual(card.erased, [(0, 1536)])


if __name__ == '__main__':
    unittest.main()
