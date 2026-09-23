#!/usr/bin/env python3
"""Find every string-pointer table in the image and print what is in it.

This document exists because a mislabelled table already cost this project
real time: the 48-entry table at 0x4018e254 was read as the runtime key map,
and it is not -- it labels the hardware panel-test screen. Its "PLAY" is trig
2. Conclusions were drawn from that for a while.

So rather than name tables by what they look like, this finds them
structurally -- runs of consecutive pointers that all land on printable
NUL-terminated strings -- and prints their contents. What a table IS remains a
separate question, and the document says so: a table is evidence of a list,
not of its purpose.

    python tools/mk1doc_strings.py --out docs/mk1
    python tools/mk1doc_strings.py --out docs/mk1 --full out/mk1/strings-full.json

The committed document records where each table is and its shape, never its
contents: the strings are Elektron's text, and 10,000 of them copied into a
public repository would be publishing the firmware's word lists and UI text
wholesale. `--full` writes the contents to a local file (`out/` is ignored)
for reading on your own machine.
"""
import argparse
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config

LOAD = 0x40000400
MIN_RUN = 6            # shorter runs are mostly coincidence
MAX_STR = 40


def read_string(img, addr):
    """-> the printable NUL-terminated string at `addr`, or None."""
    off = addr - LOAD
    if not (0 <= off < len(img)):
        return None
    end = img.find(b'\x00', off)
    if end < 0 or end - off == 0 or end - off > MAX_STR:
        return None
    s = img[off:end]
    # Accept the small control bytes Elektron uses for glyphs (e.g. 0x07 is
    # the double-arrow in "FACTORY PROJECT >> +DRIVE"), but require the run to
    # be mostly real text.
    printable = sum(1 for c in s if 32 <= c < 127)
    if printable < max(2, int(len(s) * 0.7)):
        return None
    if any(c > 127 for c in s):
        return None
    return s.decode('latin1')


def find_tables(img):
    tables = []
    n = len(img) - 4
    off = 0
    while off < n:
        val = struct.unpack_from('>I', img, off)[0]
        if read_string(img, val) is None:
            off += 4
            continue
        run, cur = [], off
        while cur < n:
            v = struct.unpack_from('>I', img, cur)[0]
            s = read_string(img, v)
            if s is None:
                break
            run.append((LOAD + cur, v, s))
            cur += 4
        if len(run) >= MIN_RUN:
            tables.append(run)
        off = cur + 4 if cur > off else off + 4
    return tables


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='docs/mk1')
    ap.add_argument('--max-tables', type=int, default=40)
    ap.add_argument('--full', metavar='PATH',
                    help='also write every table\'s strings to PATH, for local '
                         'use only (keep it out of the repository)')
    args = ap.parse_args()
    os.makedirs(os.path.join(args.out, 'facts'), exist_ok=True)

    img = open(config.main_image(), 'rb').read()
    tables = find_tables(img)
    tables.sort(key=len, reverse=True)

    recs = []
    for run in tables[:args.max_tables]:
        lengths = [len(s) for _a, _p, s in run]
        alpha = sum(1 for _a, _p, s in run if any(c.isalpha() for c in s))
        recs.append({
            'table_address': '0x%08X' % run[0][0],
            'entries': len(run),
            'first_string_address': '0x%08X' % run[0][1],
            'string_length_min': min(lengths),
            'string_length_max': max(lengths),
            # A low share usually means code bytes that happen to look like
            # pointers to text, not a real table.
            'entries_with_letters': alpha,
        })

    if args.full:
        os.makedirs(os.path.dirname(os.path.abspath(args.full)), exist_ok=True)
        with open(args.full, 'w', encoding='utf-8') as fh:
            json.dump([{'table_address': '0x%08X' % run[0][0],
                        'strings': [s for _a, _p, s in run]}
                       for run in tables], fh, indent=2)
            fh.write('\n')
        print('wrote %s (local only)' % args.full)

    facts = {
        'document': 'digitakt-mk1.string-tables',
        'schema_version': 1,
        'method': 'runs of >= %d consecutive 32-bit big-endian pointers that '
                  'all land on printable NUL-terminated strings inside the '
                  'image' % MIN_RUN,
        'caveat': 'a table found this way is evidence of a LIST, not of its '
                  'purpose. The 48-entry table at 0x4018E254 is the '
                  'hardware panel-test screen label list, NOT the runtime key '
                  'map -- reading it as the key map cost this project real '
                  'time. Do not name a table by what its contents look like.',
        'tables_found': len(tables),
        'tables_reported': len(recs),
        'tables': recs,
    }
    jpath = os.path.join(args.out, 'facts', 'strings.json')
    with open(jpath, 'w', encoding='utf-8') as fh:
        json.dump(facts, fh, indent=2)
        fh.write('\n')
    print('wrote %s' % jpath)

    L = []
    A = L.append
    A('# 07 — String-pointer tables')
    A('')
    A('Generated by `tools/mk1doc_strings.py`. Machine-readable:')
    A('`facts/strings.json`.')
    A('')
    A('Found structurally: runs of at least %d consecutive 32-bit pointers'
      % MIN_RUN)
    A('that all land on printable NUL-terminated strings in the image.')
    A('')
    A('> **A table found this way is evidence of a list, not of its purpose.**')
    A('> The 48-entry table at `0x4018E254` labels the hardware *panel-test*')
    A('> screen; it is **not** the runtime key map. Its "PLAY" is trig 2.')
    A('> Reading it as the key map cost this project real time. Do not name a')
    A('> table by what its contents look like — establish the purpose from')
    A('> the code that reads it.')
    A('')
    A('%d tables found; the %d largest are listed.' % (len(tables), len(recs)))
    A('')
    A('The strings themselves are not reproduced here: they are Elektron\'s')
    A('text. To read them, run this generator with `--full out/mk1/strings-full.json`')
    A('against your own `.syx`; `out/` is not committed.')
    A('')
    A('| table | entries | first string at | length (min–max) | entries with letters |')
    A('|---|---|---|---|---|')
    for r in recs:
        A('| `%s` | %d | `%s` | %d–%d | %d |' % (
            r['table_address'], r['entries'], r['first_string_address'],
            r['string_length_min'], r['string_length_max'],
            r['entries_with_letters']))
    A('')
    A('A table whose entries mostly lack letters is usually code bytes that')
    A('happen to look like pointers to text, not a real list.')
    A('')

    mpath = os.path.join(args.out, '07-strings.md')
    with open(mpath, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L) + '\n')
    print('wrote %s (%d tables)' % (mpath, len(tables)))


if __name__ == '__main__':
    main()
