#!/usr/bin/env python3
"""Write a file into the +Drive's ekFS, offline.

    python tools/ekfsadd.py IMAGE [FILES...] [--dir incoming] [--format]
                            [--check] [--raw] [--repair] [--base N]

The filesystem itself -- the checksum, the inode and directory layout, the
three directory indexes and the sample format, and how each was recovered
from the firmware -- is documented in emu/ekfsformat.py, which holds the
code. It lives under emu/ because the portable app formats a fresh card with
it on its first run and does not ship tools/. Every name the library defines
is re-exported here, so `import ekfsadd` keeps working.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu.ekfsformat import *                      # noqa: E402,F401,F403
from emu.ekfsformat import (                      # noqa: E402
    AUDIO_EXTENSIONS, REGION, TYPE_FILE, Ekfs, find_dir, format_image)


def main():
    ap = argparse.ArgumentParser(
        description='write files into a +Drive ekFS image')
    ap.add_argument('image')
    ap.add_argument('files', nargs='*')
    ap.add_argument('--base', type=lambda s: int(s, 0), default=REGION)
    ap.add_argument('--dir', default='incoming',
                    help='target directory under the root (default: incoming)')
    ap.add_argument('--type', type=lambda s: int(s, 0), default=TYPE_FILE)
    ap.add_argument('--check', action='store_true',
                    help='verify the superblock checksum and stop')
    ap.add_argument('--format', action='store_true',
                    help='lay down a fresh ekFS first (DESTROYS the sample '
                         'region)')
    ap.add_argument('--raw', action='store_true',
                    help='store .wav files byte for byte instead of '
                         'converting them to the +Drive sample format (the '
                         'firmware will not load a raw WAV as a sample)')
    ap.add_argument('--repair', action='store_true',
                    help='rebuild every directory index and correct '
                         'directory link counts, for a card written by an '
                         'earlier version of this tool')
    a = ap.parse_args()

    if a.format:
        format_image(a.image, a.base)
        print('formatted the sample region of %s' % a.image)

    fs = Ekfs(a.image, a.base, write=bool(a.files) or a.repair)
    if a.repair:
        for d, n, (old, new) in fs.repair():
            print('  directory inode %-4d %3d entries, indexes rebuilt, '
                  'links %d%s' % (d, n, new,
                                  '' if old == new else ' (was %d)' % old))
        fs.f.flush()
    print('ekFS v%d: %d inodes (%d used), %d blocks (%d used)'
          % (fs.version, fs.inode_count, fs.used('inode'),
             fs.block_count, fs.used('block')))
    print('superblock checksum 0x%08x  %s'
          % (fs.stored_checksum, 'OK' if fs.checksum_ok() else 'MISMATCH'))
    if a.check or not a.files:
        return 0 if fs.checksum_ok() else 1

    target = find_dir(fs, a.dir)
    if target is None:
        raise SystemExit('no directory %r under the root' % a.dir)
    print('writing into /%s (inode %d)' % (a.dir, target))
    for p in a.files:
        with open(p, 'rb') as fh:
            data = fh.read()
        is_audio = os.path.splitext(p)[1].lower() in AUDIO_EXTENSIONS
        if is_audio and not a.raw:
            ino, name, rate, frames = fs.add_sample(target, p, data)
            print('  %-32s sample, %d frames at %d Hz -> inode %d'
                  % (name, frames, rate, ino))
        else:
            name = os.path.basename(p)
            ino = fs.add_file(target, name, data, a.type)
            print('  %-32s %8d bytes -> inode %d' % (name, len(data), ino))
    print('superblock resealed as 0x%08x' % fs.stored_checksum)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
