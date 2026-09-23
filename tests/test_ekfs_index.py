"""The +Drive writer must build directory indexes exactly as the firmware does.

It used to write them as zeros, and the firmware lists directories only
through them, so SETTINGS > SAMPLES opened on an empty list. Every expected
value below is firmware-measured, not produced by the code under test:

- FW_BLOCKS are the directory blocks of a card the firmware formatted itself
  (FORMAT +DRIVE under emulation). Past byte 64 each block is all zeros.
- FW_HASH are the keys the firmware wrote into root's hash index.
- FW_CMP is FUN_400e8606's sign for each pair, from running the firmware's
  own routine under Unicorn -- including its quirk of never reading the
  character after an equal digit run.
"""

import importlib.util
import os
import struct
import tempfile
import unittest
from pathlib import Path

TOOL = Path("tools/ekfsadd.py")

FW_HASH = {b'.': 0x71D73E48, b'..': 0x7C37AA9E, b'incoming': 0x8E2A7BCC,
           b'factory': 0xE90F5D74}

FW_CMP = [
    (b'a', b'b', -1), (b'b', b'a', 1), (b'a', b'a', 0), (b'a', b'ab', -1),
    (b'ab', b'a', 1), (b'Kick', b'kick', 0), (b'kick', b'Kick', 0),
    (b'_x', b'Zx', 1), (b'_x', b'zx', 1), (b'bell2', b'bell10', -1),
    (b'bell10', b'bell2', 1), (b'bell02', b'bell2', 1),
    (b'bell2', b'bell02', -1), (b'bell002', b'bell02', 1), (b'x9', b'x10', -1),
    (b'a1b', b'a1c', 0), (b'a1c', b'a1b', 0), (b'a01b', b'a1c', 1),
    (b'a1', b'a1b', 0), (b'10', b'9', 1), (b'9', b'10', -1), (b'1a', b'1b', 0),
    (b'1', b'01', -1), (b'track 1.wav', b'track 12.wav', -1),
    (b'snare_01', b'snare_1', 1), (b'caf\xc3\xa9', b'cafe', -1),
    (b'', b'', 0), (b'', b'a', -1), (b'a', b'', 1),
    (b'abc123def', b'abc123dEF', 0), (b'abc123xyz', b'abc124', -1),
    (b'x0', b'x', 1), (b'x', b'x0', -1), (b'v1.2.10', b'v1.2.9', 1),
    (b'a1bz', b'a1ca', 1), (b'a1b2', b'a1c3', -1), (b'k12x', b'k12y', 0),
    (b'k12', b'k12x', 0), (b'k12x', b'k12', 0), (b'k12xa', b'k12yb', -1),
    (b'1', b'1', 0), (b'1x', b'1', 0), (b'v1.2.3', b'v1.2.30', -1),
    (b'kick 1.wav', b'kick 1.aif', 1),
    (b'Kick_808_01.wav', b'kick_808_1.wav', 1), (b'snare 2', b'snare 10', -1),
    (b'hat001', b'hat01x', 1),
]

FW_BLOCKS = {
    96: '00000002000c01012e00000000000002000c02012e2e00000100000000100701'
        '666163746f727900000000033fd80801696e636f6d696e670000000000000000',
    97: '000400000000000071d73e48000000007c37aa9e0000000c8e2a7bcc00000028'
        'e90f5d7400000018000000000000000000000000000000000000000000000000',
    98: '00040000000000002e00534f000000002e2e00530000000c6661637400000018'
        '696e636f00000028000000000000000000000000000000000000000000000000',
    99: '00040000000000000000000200000000000000020000000c0000000300000028'
        '0100000000000018000000000000000000000000000000000000000000000000',
    100: '00000003000c01012e000000000000023ff402012e2e00000000000000000000'
         '0000000000000000000000000000000000000000000000000000000000000000',
    101: '000200000000000071d73e48000000007c37aa9e0000000c0000000000000000'
         '0000000000000000000000000000000000000000000000000000000000000000',
    102: '00020000000000002e00534f000000002e2e00530000000c0000000000000000'
         '0000000000000000000000000000000000000000000000000000000000000000',
    103: '0002000000000000000000020000000c00000003000000000000000000000000'
         '0000000000000000000000000000000000000000000000000000000000000000',
}


def _load():
    spec = importlib.util.spec_from_file_location("ekfsadd", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sign(x):
    return (x > 0) - (x < 0)


@unittest.skipUnless(TOOL.exists(), "tools/ekfsadd.py absent")
class EkfsIndexTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ek = _load()

    def _fresh(self):
        """A formatted image with the region at sector 0: ~8 MB instead of
        ~950 MB. Nothing in a directory block depends on the region base."""
        fd, path = tempfile.mkstemp(suffix=".img")
        os.close(fd)
        self.addCleanup(os.remove, path)
        self.ek.format_image(path, base=0)
        return path

    def test_hash_matches_firmware(self):
        for name, want in FW_HASH.items():
            self.assertEqual(self.ek.dx_hack_hash(name), want, name)

    def test_natcmp_matches_firmware(self):
        for a, b, want in FW_CMP:
            self.assertEqual(_sign(self.ek.natcmp(a, b)), want, (a, b))

    def test_format_reproduces_firmware_directories(self):
        fs = self.ek.Ekfs(self._fresh(), base=0)
        self.addCleanup(fs.close)
        for blk, hexed in FW_BLOCKS.items():
            got = bytes(fs.block(blk))
            self.assertEqual(got[:64], bytes.fromhex(hexed), "block %d" % blk)
            self.assertFalse(any(got[64:]), "block %d has trailing data" % blk)

    def test_adding_files_updates_every_index(self):
        path = self._fresh()
        fs = self.ek.Ekfs(path, base=0, write=True)
        self.addCleanup(fs.close)
        inc = self.ek.find_dir(fs, "incoming")
        for name in ("snare 10.wav", "Kick.wav", "snare 2.wav"):
            fs.add_file(inc, name, b"RIFF" + b"\0" * 100)
        raw = fs.inode(inc)
        blocks = [fs.physical(raw, lg) for lg in self.ek.INDEX_LOGICAL]
        counts = [struct.unpack(">H", bytes(fs.block(b))[:2])[0]
                  for b in blocks]
        self.assertEqual(counts, [5, 5, 5])
        # name index: '.' '..' first, then natural, case-insensitive order
        name_idx = bytes(fs.block(blocks[1]))
        by_loc = {loc: n for loc, _i, n, _t in fs.dir_entries(inc)}
        order = [by_loc[struct.unpack(">I", name_idx[12 + 8 * i:16 + 8 * i])[0]]
                 for i in range(5)]
        self.assertEqual(order, [b".", b"..", b"Kick.wav", b"snare 2.wav",
                                 b"snare 10.wav"])

    def test_adding_a_file_leaves_the_directory_link_count(self):
        """+0x02 is a link count. The firmware's insert never touches the
        directory's own; this tool used to bump it once per file."""
        path = self._fresh()
        fs = self.ek.Ekfs(path, base=0, write=True)
        self.addCleanup(fs.close)
        inc = self.ek.find_dir(fs, "incoming")
        fs.add_file(inc, "a.wav", b"x")
        fs.add_file(inc, "b.wav", b"y")
        self.assertEqual(struct.unpack(">H", bytes(fs.inode(inc))[2:4])[0], 2)

    def test_repair_restores_zeroed_indexes(self):
        """What a card written by the old tool looks like, and its repair."""
        path = self._fresh()
        fs = self.ek.Ekfs(path, base=0, write=True)
        self.addCleanup(fs.close)
        inc = self.ek.find_dir(fs, "incoming")
        fs.add_file(inc, "bell.wav", b"z")
        before = {b: bytes(fs.block(b)) for b in range(96, 104)}
        for d in (2, inc):                        # the old tool's damage
            raw = fs.inode(d)
            for lg in self.ek.INDEX_LOGICAL:
                fs.put_block(fs.physical(raw, lg),
                             bytearray(self.ek.BLOCK_BYTES))
        struct.pack_into(">H", raw, 0x02, 3)
        fs.put_inode(inc, raw)
        report = {d: links for d, _n, links in fs.repair()}
        self.assertEqual(report[inc], (3, 2))
        for b, data in before.items():
            self.assertEqual(bytes(fs.block(b)), data, "block %d" % b)


if __name__ == "__main__":
    unittest.main()
