#!/usr/bin/env python3
"""Read the +Drive's sample filesystem out of a raw card image.

    python tools/ekfs.py IMAGE [--ls PATH] [--inode N] [--bitmaps]

Read-only. Everything it knows is in docs/mk1/10-plusdrive.md, recovered from
the firmware's own mkfs and allocator and checked against an image that the
firmware formatted under emulation.

The region base is not searched for: FORMAT +DRIVE erases the sample region
starting at sector 0x1c0000 and mkfs writes the superblock at exactly that
sector, so the base is a constant of the layout, not of a particular image.
A `--base` override is there for a card whose regions ever move.
"""
import argparse
import struct
import sys

SECTOR = 512
REGION = 0x1C0000                 # sample-region base, in sectors
MAGIC = b'ekFS'
INODE_SIZE = 0x80
INODES_PER_CHUNK = 128
CHUNK_SECTORS = 0x20              # 16 KB, also the data block size
BLOCK_BYTES = CHUNK_SECTORS * SECTOR
ROOT_INODE = 2
RAM_INODE = 0x01000000            # at or above this, an inode is never on card
TYPE_DIR = 1


class BadImage(Exception):
    pass


class Ekfs:
    def __init__(self, path, base=REGION):
        self.f = open(path, 'rb')
        self.base = base
        sb = self.sectors(base, 1)
        if sb[:4] != MAGIC:
            raise BadImage('no ekFS superblock at sector 0x%x (found %r)'
                           % (base, sb[:4]))
        u = lambda o: struct.unpack('>I', sb[o:o + 4])[0]      # noqa: E731
        self.version = u(0x04)
        self.bitmap_bytes = u(0x08)
        self.inode_count = u(0x0c)
        self.block_count = u(0x10)
        self.inode_bitmap_off = u(0x14)
        self.block_bitmap_off = u(0x18)
        self.inode_table_off = u(0x1c)
        self.data_off = u(0x20)
        self.checksum = u(0x1fc)

    def sectors(self, sector, n):
        self.f.seek(sector * SECTOR)
        return self.f.read(n * SECTOR)

    # -- inodes ---------------------------------------------------------
    def inode(self, n):
        """-> the 128 raw bytes of inode n, or None when it is RAM-only."""
        if n >= RAM_INODE:
            return None
        if not 2 <= n < self.inode_count:
            raise BadImage('inode %d out of range' % n)
        chunk = n // INODES_PER_CHUNK
        sector = self.base + self.inode_table_off + chunk * CHUNK_SECTORS
        raw = self.sectors(sector, CHUNK_SECTORS)
        off = (n % INODES_PER_CHUNK) * INODE_SIZE
        return raw[off:off + INODE_SIZE]

    # -- directories ----------------------------------------------------
    def block(self, n):
        return self.sectors(self.base + self.data_off + n * CHUNK_SECTORS,
                            CHUNK_SECTORS)

    @staticmethod
    def parse_dir(buf):
        """-> [(inode, name, type)] from one directory block.

        Entry: u32 inode, u16 record length, u8 name length, u8 type, name.
        The record length of the last entry runs to the end of the block.
        """
        out, o = [], 0
        while o + 8 <= len(buf):
            ino, rec, nlen, typ = struct.unpack('>IHBB', buf[o:o + 8])
            if rec < 8 or o + rec > len(buf):
                break
            if ino:
                out.append((ino, buf[o + 8:o + 8 + nlen].decode('latin-1'), typ))
            o += rec
        return out

    def find_root_dir_block(self, limit=4096):
        """The root's data block, by looking for its own '.' and '..'.

        The root inode's block list has not been decoded yet (see the open
        list in the document), so this scans the start of the data area for a
        directory block whose first two entries are '.' and '..' pointing at
        inode 2. Honest about being a search rather than a lookup.
        """
        for n in range(limit):
            entries = self.parse_dir(self.block(n))
            if len(entries) >= 2:
                (i1, n1, _), (i2, n2, _) = entries[0], entries[1]
                if n1 == '.' and n2 == '..' and i1 == ROOT_INODE:
                    return n, entries
        return None, []

    def describe(self):
        yield 'ekFS version %d at sector 0x%x' % (self.version, self.base)
        yield '  inode bitmap  sector 0x%x   %d bytes' % (
            self.base + self.inode_bitmap_off, self.bitmap_bytes)
        yield '  block bitmap  sector 0x%x   %d bytes' % (
            self.base + self.block_bitmap_off, self.bitmap_bytes)
        yield '  inode table   sector 0x%x   %d inodes x %d bytes = %.1f MB' % (
            self.base + self.inode_table_off, self.inode_count, INODE_SIZE,
            self.inode_count * INODE_SIZE / 1048576.0)
        yield '  data blocks   sector 0x%x   %d blocks x %d KB = %.1f MB' % (
            self.base + self.data_off, self.block_count, BLOCK_BYTES // 1024,
            self.block_count * BLOCK_BYTES / 1048576.0)
        yield '  superblock checksum 0x%08x' % self.checksum

    def used(self, which):
        off = (self.inode_bitmap_off if which == 'inode'
               else self.block_bitmap_off)
        raw = self.sectors(self.base + off, self.bitmap_bytes // SECTOR)
        return sum(bin(b).count('1') for b in raw)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('image')
    ap.add_argument('--base', type=lambda s: int(s, 0), default=REGION)
    ap.add_argument('--ls', action='store_true', help='list the root directory')
    ap.add_argument('--inode', type=int, help='hexdump one inode')
    ap.add_argument('--bitmaps', action='store_true', help='count used bits')
    a = ap.parse_args()

    try:
        fs = Ekfs(a.image, a.base)
    except BadImage as e:
        print('%s: %s' % (a.image, e), file=sys.stderr)
        return 1
    for line in fs.describe():
        print(line)

    if a.bitmaps:
        print('\ninodes in use: %d' % fs.used('inode'))
        print('blocks in use: %d of %d' % (fs.used('block'), fs.block_count))

    if a.ls:
        n, entries = fs.find_root_dir_block()
        if n is None:
            print('\nno root directory block found')
        else:
            print('\nroot directory, data block %d:' % n)
            for ino, name, typ in entries:
                kind = 'dir ' if typ == TYPE_DIR else 'type%d' % typ
                where = ' (RAM only)' if ino >= RAM_INODE else ''
                print('  %-10s %s inode %d (0x%x)%s'
                      % (name, kind, ino, ino, where))

    if a.inode is not None:
        raw = fs.inode(a.inode)
        if raw is None:
            print('\ninode %d is RAM-only, nothing on the card' % a.inode)
        else:
            print('\ninode %d:' % a.inode)
            for o in range(0, INODE_SIZE, 16):
                row = raw[o:o + 16]
                txt = ''.join(chr(c) if 32 <= c < 127 else '.' for c in row)
                print('  %02x: %s  |%s|' % (o, row.hex(' '), txt))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
