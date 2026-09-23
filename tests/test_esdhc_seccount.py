"""EXT_CSD SEC_COUNT must read back, the way the firmware reads it, as the
card's real capacity.

sd_bringup takes SEC_COUNT as a BIG-endian longword at EXT_CSD+0xD4 and stores
it as the sector count that emu's write primitive range-checks against. Packed
the other way round, 0x00760000 arrives as 0x00007600 and the card claims
14.75 MB, which silently drops every +Drive write above that -- the write path
returns -10 before issuing any command, so nothing appears in a card log.
"""
import struct
import unittest

from emu.esdhc import PART, Card, SEC_COUNT, ext_csd

# The +Drive's highest sector, from the region map the firmware's own
# FORMAT +DRIVE erases (docs/mk1/10-plusdrive.md).
HIGHEST_PLUSDRIVE_SECTOR = 3816527


def firmware_reads(ext, offset):
    """What `move.l (base+offset).l,D0` sees: a big-endian longword."""
    return struct.unpack('>I', ext[offset:offset + 4])[0]


class SecCountTest(unittest.TestCase):
    def test_default_capacity_reads_back(self):
        # The default is the whitelisted part's SLC sector count (0x3B0000):
        # the firmware's part check compares EXT_CSD's count against it, and
        # the MLC figure (0x760000) failed that check, so the +Drive was
        # never mounted. See emu/esdhc.py PART.
        self.assertEqual(firmware_reads(ext_csd(), SEC_COUNT),
                         PART['sectors_slc'])
        self.assertEqual(PART['sectors_slc'], 0x003B0000)

    def test_capacity_is_whatever_the_card_was_given(self):
        for sectors in (0x00760000, 0x00400000, 1234567):
            self.assertEqual(firmware_reads(ext_csd(sectors), SEC_COUNT), sectors)

    def test_card_exposes_the_same_value(self):
        card = Card()
        self.assertEqual(firmware_reads(card.ext_csd, SEC_COUNT), card.blocks)

    def test_capacity_covers_the_whole_plusdrive(self):
        card = Card()
        self.assertGreater(firmware_reads(card.ext_csd, SEC_COUNT),
                           HIGHEST_PLUSDRIVE_SECTOR)

    def test_sec_count_is_the_jedec_offset(self):
        self.assertEqual(SEC_COUNT, 212)


if __name__ == '__main__':
    unittest.main()
