"""The eMMC image file: writes land in it at flush, reads come back after reopen."""
import os
import tempfile
import unittest

from emu.esdhc import Card


def _stale(path):
    """Push the file's times into the past, so a later stamp is visibly new."""
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))


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
            c.close()
            self.assertEqual(os.path.getsize(p), 0x200000 + 512)
            c2 = Card(path=p)
            self.assertEqual(c2.data_for(18, 0x1000, 512), payload)
            self.assertEqual(bytes(c2.data_for(18, 0, 4)), b'\xbe\xef\xba\xce')
            self.assertEqual(c2.data_for(18, 7, 512), b'\0' * 512)   # unwritten reads zero
            c2.close()

    def test_close_releases_the_file_and_is_idempotent(self):
        # A mapped file cannot be replaced or deleted on Windows; close() is
        # what lets a run hand the card on (first run, reset to factory).
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'plusdrive.img')
            c = Card(path=p)
            c.close()
            c.close()
            os.replace(p, p + '.old')
            os.remove(p + '.old')
            self.assertEqual(c.data_for(18, 0, 512), b'\0' * 512)

    def test_flush_of_a_closed_card_with_writes_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'plusdrive.img')
            c = Card(path=p)
            c.close()
            c.flush()                                    # nothing pending: fine
            c.write_data(25, 0, b'x' * 512)
            with self.assertRaises(ValueError):
                c.flush()

    def test_flush_moves_the_modification_time(self):
        # Writes through a memory map leave the mtime alone on Windows, so
        # flush stamps it; bootstrap.card_stamp relies on that.
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'plusdrive.img')
            with open(p, 'wb') as f:
                f.write(bytes(4096))
            _stale(p)
            c = Card(path=p)
            c.write_data(25, 1, b'y' * 512)
            c.flush()
            c.close()
            self.assertGreater(os.stat(p).st_mtime_ns, 1_000_000_000)

    def test_flush_with_nothing_written_leaves_the_file_alone(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'plusdrive.img')
            with open(p, 'wb') as f:
                f.write(bytes(4096))
            _stale(p)
            c = Card(path=p)
            c.flush()
            c.close()
            self.assertEqual(os.stat(p).st_mtime_ns, 1_000_000_000)

    def test_no_path_is_in_memory(self):
        c = Card()
        c.write_data(25, 3, b'x' * 512)
        c.flush()                                        # no-op, no error
        self.assertEqual(c.data_for(18, 3, 512), b'x' * 512)

    def test_a_flush_that_dies_half_way_still_moves_the_stamp(self):
        # Cancel terminates the worker and a logoff ends the panel mid-save;
        # a card half written but with its old size and mtime would pair
        # with the older snapshot. The stamp must move before any byte does.
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'plusdrive.img')
            with open(p, 'wb') as f:
                f.write(bytes(8192))
            _stale(p)
            c = Card(path=p)
            real = c.image
            c.image = _DiesOnWrite(real)
            c.write_data(25, 2, b'z' * 512)
            with self.assertRaises(OSError):
                c.flush()
            c.image = real
            c.close()
            self.assertNotEqual(os.stat(p).st_mtime_ns, 1_000_000_000)
            with open(p, 'rb') as f:
                self.assertEqual(f.read(), bytes(8192))  # nothing landed


class _DiesOnWrite:
    """Stands in for the card's memory map; the first write fails."""

    def __init__(self, real):
        self.real = real

    def __len__(self):
        return len(self.real)

    def __getitem__(self, key):
        return self.real[key]

    def __setitem__(self, key, value):
        raise OSError('the process died here')

    def flush(self):
        return self.real.flush()

    def close(self):
        return self.real.close()


MB = 1 << 20


@unittest.skipUnless(os.name == 'nt', 'NTFS sparse files are Windows-only')
class SparseCardTest(unittest.TestCase):
    """On NTFS the card stays sparse: growth and erases allocate nothing.

    Skipped where the temp directory cannot hold a sparse file (the helper
    then falls back to plain files, which the tests above cover).
    """

    def setUp(self):
        from emu import sparse
        self.sparse = sparse
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        probe = os.path.join(self.dir, 'probe')
        with open(probe, 'wb') as fh:
            if not sparse.make_sparse(fh):
                self.skipTest('no sparse files in %s' % self.dir)

    def test_a_new_card_is_sparse_and_grows_without_allocating(self):
        p = os.path.join(self.dir, 'plusdrive.img')
        c = Card(path=p)
        self.assertTrue(self.sparse.is_sparse(p))
        c.write_data(25, 0x100000, b'\x5a' * 512)        # byte 512 MB
        c.flush()
        c.close()
        self.assertEqual(os.path.getsize(p), 512 * MB + 512)
        self.assertLess(self.sparse.allocated_size(p), 4 * MB)
        c2 = Card(path=p)
        self.assertEqual(c2.data_for(18, 0x100000, 512), b'\x5a' * 512)
        c2.close()

    def test_an_erase_deallocates_instead_of_writing_zeros(self):
        p = os.path.join(self.dir, 'plusdrive.img')
        with open(p, 'w+b') as fh:
            self.assertTrue(self.sparse.make_sparse(fh))
            self.sparse.extend(fh, 64 * MB)
            fh.seek(MB)
            fh.write(b'\xaa' * (8 * MB))
            fh.flush()
            os.fsync(fh.fileno())        # NTFS allocates cached writes late
        before = self.sparse.allocated_size(p)
        self.assertGreaterEqual(before, 8 * MB)
        c = Card(path=p)
        c.erase(MB, 9 * MB)
        c.write_data(25, (4 * MB) // 512, b'\x77' * 512)  # a write after it wins
        c.flush()
        c.close()
        self.assertLess(self.sparse.allocated_size(p), 2 * MB)
        with open(p, 'rb') as fh:
            fh.seek(MB)
            data = fh.read(8 * MB)
        self.assertEqual(data[3 * MB:3 * MB + 512], b'\x77' * 512)
        self.assertEqual(data[:3 * MB].count(0), 3 * MB)
        self.assertEqual(data[3 * MB + 512:].count(0), 5 * MB - 512)

    def test_a_plain_card_is_still_zeroed_by_writing(self):
        p = os.path.join(self.dir, 'plusdrive.img')
        with open(p, 'wb') as fh:
            fh.write(b'\xee' * (2 * MB))
        self.assertFalse(self.sparse.is_sparse(p))
        c = Card(path=p)
        c.erase(0, MB)
        c.flush()
        c.close()
        with open(p, 'rb') as fh:
            data = fh.read()
        self.assertEqual(data[:MB], bytes(MB))
        self.assertEqual(data[MB:], b'\xee' * MB)


if __name__ == '__main__':
    unittest.main()
