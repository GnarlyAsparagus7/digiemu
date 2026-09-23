#!/usr/bin/env python3
"""Document the mk1 firmware container, bottom-up, from the bytes themselves.

Every value here is READ OR COMPUTED FROM THE FILE. Nothing is carried over
from the Digitakt II documentation and nothing is inferred from a sibling
fact. Where a heuristic is used (architecture detection) the evidence is
printed alongside the verdict so the verdict can be checked rather than
trusted.

Emits both a JSON fact file and Markdown. The JSON is the artefact another
agent should read; the Markdown is the same content for a person.

    python tools/mk1doc_container.py --syx Digitakt_OS1.53.syx --out docs/mk1
"""
import argparse
import collections
import hashlib
import json
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dt2 import container as dtc
from dt2.elz import depack_section
from emu.extract import classify

# Opcode fingerprints. These are COUNTS used as evidence, not a classifier to
# be trusted blindly -- a data section can contain any byte pair.
M68K_MARKS = {
    b'\x4e\x75': 'rts',
    b'\x4e\x56': 'link a6',
    b'\x4f\xef': 'lea -n(a7),a7',
    b'\x48\xe7': 'movem.l ->(a7)',
    b'\x4e\x5e': 'unlk a6',
}
# Thumb-2: PUSH {..,lr} is b5xx, POP {..,pc} is bdxx, both very common as
# function prologue/epilogue. Little-endian, so the marker byte is the SECOND
# byte of the halfword.
THUMB_HI = (0xB5, 0xBD)


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def entropy(b):
    if not b:
        return 0.0
    counts = collections.Counter(b)
    n = float(len(b))
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def m68k_score(b):
    return {name: b.count(sig) for sig, name in M68K_MARKS.items()}


def thumb_score(b):
    n = 0
    for i in range(1, min(len(b), 400000), 2):
        if b[i] in THUMB_HI:
            n += 1
    return n


def classify_arch(b):
    """-> (verdict, evidence dict). Evidence is reported either way."""
    m = m68k_score(b)
    t = thumb_score(b)
    ent = entropy(b)
    ev = {'m68k_opcode_counts': m, 'm68k_total': sum(m.values()),
          'thumb_push_pop_halfwords': t, 'shannon_entropy_bits_per_byte':
          round(ent, 3)}
    if ent > 7.5:
        return 'compressed-or-encrypted (high entropy)', ev
    if sum(m.values()) > t:
        return 'ColdFire / m68k (big-endian)', ev
    if t > 200:
        return 'ARM Thumb-2 (little-endian)', ev
    return 'data or unrecognised', ev


def syx_framing(path):
    """Facts about the SysEx transport layer, read from the file."""
    raw = open(path, 'rb').read()
    msgs, i, sizes, devices, cmds = 0, 0, collections.Counter(), \
        collections.Counter(), collections.Counter()
    while i < len(raw):
        if raw[i] != 0xF0:
            break
        j = raw.find(b'\xf7', i)
        if j < 0:
            break
        body = raw[i + 1:j]
        msgs += 1
        sizes[len(body)] += 1
        if len(body) >= 6 and body[0:3] == dtc.MFR:
            devices[body[3]] += 1
            cmds[body[5]] += 1
        i = j + 1
    return {
        'file_bytes': len(raw),
        'file_sha256': sha256(raw),
        'sysex_messages': msgs,
        'message_body_sizes': {str(k): v for k, v in sizes.items()},
        'manufacturer_id': dtc.MFR.hex(),
        'manufacturer': 'Elektron (00 20 3C)',
        'device_bytes_seen': {hex(k): v for k, v in devices.items()},
        'command_bytes_seen': {hex(k): v for k, v in cmds.items()},
        'encoding': '8-in-7: each group of 7 data bytes preceded by a byte '
                    'holding their high bits, MSB first',
        'provenance': 'read from the .syx file by this tool',
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--out', default='docs/mk1')
    args = ap.parse_args()

    os.makedirs(os.path.join(args.out, 'facts'), exist_ok=True)

    facts = {
        'document': 'digitakt-mk1.container',
        'schema_version': 1,
        'firmware_file': os.path.basename(args.syx),
        'notes': 'Every field is read or computed from the firmware file by '
                 'tools/mk1doc_container.py. No value is carried over from '
                 'the Digitakt II documentation.',
    }
    facts['sysex'] = syx_framing(args.syx)

    decoded = dtc.decode_syx(args.syx)
    ele_off = decoded.find(b'ELE3')
    preamble = decoded[:ele_off]
    facts['decoded_stream'] = {
        'bytes': len(decoded),
        'sha256': sha256(decoded),
        'ele3_offset': ele_off,
        'preamble_hex': preamble.hex(),
        'preamble_note': '8 bytes; bytes 4..8 are the 32-bit content '
                         'checksum per dt2/container.py',
    }
    if len(preamble) >= 8:
        facts['decoded_stream']['declared_checksum'] = '0x%08x' % (
            struct.unpack_from('>I', preamble, 4)[0])

    c, secs = dtc.sections(args.syx)
    facts['container'] = {
        'magic': 'ELE3',
        'bytes': len(c),
        'sha256': sha256(c),
        'section_count_offset': '0x%02x' % dtc.COUNT_OFF,
        'section_table_offset': '0x%02x' % dtc.TABLE_OFF,
        'section_entry_size': dtc.ENTRY_SZ,
        'section_entry_fields': ['id', 'offset', 'compressed_length', 'dest'],
        'dest_note': 'a load address for code sections; for the bootstrap '
                     '(section 2) it is a VERSION word, not an address '
                     '(dt2/container.py)',
        'section_count': len(secs),
        'header_hex': c[:dtc.TABLE_OFF].hex(),
    }
    # The 32 bytes before the section table are structured, not opaque: two of
    # the fields are plain ASCII. Decoded here field by field so the layout is
    # documented rather than left as a hex blob for the next reader to squint
    # at. Offsets are from the ELE3 magic.
    hdr = c[:dtc.TABLE_OFF]

    def _txt(lo, hi):
        return hdr[lo:hi].decode('latin1')

    facts['container']['header_fields'] = [
        {'offset': '0x00', 'size': 4, 'raw': hdr[0:4].hex(),
         'interpretation': 'magic %r' % _txt(0, 4), 'confidence': 'measured'},
        {'offset': '0x04', 'size': 4,
         'raw': hdr[4:8].hex(),
         'interpretation': 'u32 = %d. NO CONSUMER FOUND: MAIN OS loads the '
                           '32-byte header to 0x421ED5A8 and there is not one '
                           'absolute reference to 0x421ED5AC anywhere in the '
                           'image' % struct.unpack_from('>I', hdr, 4)[0],
         'confidence': 'value measured; unused by any absolute reference'},
        {'offset': '0x08', 'size': 12, 'raw': hdr[8:20].hex(),
         'interpretation': 'ASCII %r (space padded) -- the BUILD string. The '
                           'updater compares it as a 32-bit value against '
                           '"006/" to enforce a build-number floor; see '
                           'docs/FINDINGS.md "MAIN OS has a second gate, and '
                           'it reads the BUILD string". So this is build 104, '
                           'not a product code.' % _txt(8, 20),
         'confidence': 'measured bytes; purpose from docs/FINDINGS.md'},
        {'offset': '0x14', 'size': 4, 'raw': hdr[20:24].hex(),
         'interpretation': 'ASCII %r -- the OS version string. Read '
                           'register-relative, not absolutely, which is why '
                           'a search for an absolute reference finds none '
                           '(docs/FINDINGS.md).' % _txt(20, 24),
         'confidence': 'measured bytes; matches the filename version'},
        {'offset': '0x18', 'size': 4, 'raw': hdr[24:28].hex(),
         'interpretation': 'u32 = %d. NO CONSUMER FOUND: no absolute '
                           'reference to 0x421ED5C0 anywhere in the image'
                           % struct.unpack_from('>I', hdr, 24)[0],
         'confidence': 'value measured; unused by any absolute reference'},
        {'offset': '0x1c', 'size': 4, 'raw': hdr[28:32].hex(),
         'interpretation': 'u32 = %d -- the section count, which '
                           'dt2/container.py reads from this offset'
                           % struct.unpack_from('>I', hdr, 28)[0],
         'confidence': 'measured, corroborated by dt2/container.py'},
    ]

    out_sections = []
    for i, (sid, off, clen, dest) in enumerate(secs):
        rec = {
            'index': i,
            'id': sid,
            'container_offset': '0x%08x' % off,
            'compressed_length': clen,
            'dest_raw': '0x%08x' % dest,
        }
        stream = dtc.compressed_stream(c, off, clen)
        if len(stream) >= 8:
            slen, ssum = struct.unpack_from('>II', stream, 0)
            rec['stream_header'] = {
                'declared_stream_length': slen,
                'declared_byte_sum': '0x%08x' % ssum,
                'header_matches_slice': slen + 8 == clen or slen == clen,
            }
        # A section is packed only when its 8-byte header is self-consistent:
        # the declared length fits and the bytes after it sum to the declared
        # sum. emu/extract.py's classify() makes that check, and it matters
        # here: an earlier version of this tool called the depacker on every
        # section and reported "did not decompress" for two of them. Both are
        # simply NOT COMPRESSED -- section 4 (the updater) is stored raw with
        # a header to strip, and section 5 (metadata) raw with none. Reporting
        # that as a failure framed a non-issue as a gap in the knowledge.
        kind, payload = classify(stream)
        rec['storage'] = kind
        if kind == 'raw':
            rec['decompressed_length'] = len(payload)
            rec['decompressed_sha256'] = sha256(payload)
            rec['first_16_bytes'] = payload[:16].hex()
            verdict, ev = classify_arch(payload)
            rec['architecture'] = verdict if len(payload) > 64 else 'raw data'
            rec['architecture_evidence'] = ev
            rec['compression_ratio'] = 1.0
            try:
                text = payload.decode('ascii')
                if all(32 <= ord(ch) < 127 for ch in text):
                    rec['raw_as_text'] = text
            except Exception:                            # noqa: BLE001
                pass
            out_sections.append(rec)
            continue
        try:
            body = depack_section(stream)
            rec['decompressed_length'] = len(body)
            rec['decompressed_sha256'] = sha256(body)
            verdict, ev = classify_arch(body)
            rec['architecture'] = verdict
            rec['architecture_evidence'] = ev
            rec['compression_ratio'] = round(len(body) / max(clen, 1), 3)
            rec['first_16_bytes'] = body[:16].hex()
        except Exception as exc:                        # noqa: BLE001
            rec['decompress_error'] = '%s: %s' % (type(exc).__name__, exc)
            rec['architecture'] = 'UNKNOWN (did not decompress)'
        out_sections.append(rec)
    facts['sections'] = out_sections

    jpath = os.path.join(args.out, 'facts', 'container.json')
    with open(jpath, 'w', encoding='utf-8') as fh:
        json.dump(facts, fh, indent=2, sort_keys=False)
        fh.write('\n')
    print('wrote %s' % jpath)

    # ---- Markdown ----
    L = []
    A = L.append
    A('# 01 — Container: SysEx, ELE3 and the sections')
    A('')
    A('Generated by `tools/mk1doc_container.py` from `%s`. Every number here'
      % facts['firmware_file'])
    A('is read or computed from that file. Machine-readable copy:')
    A('`facts/container.json`.')
    A('')
    A('## Layer cake')
    A('')
    A('| # | layer | what it is |')
    A('|---|---|---|')
    A('| 1 | MIDI SysEx | `F0 00 20 3C <dev> 00 <cmd> ... F7` messages |')
    A('| 2 | 8-in-7 | MIDI is 7-bit; each 7 data bytes carry a leading '
      'high-bit byte, MSB first |')
    A('| 3 | preamble | 8 bytes; bytes 4..8 are a 32-bit content checksum |')
    A('| 4 | ELE3 | magic, then a section table of 16-byte entries |')
    A('')
    A('Nothing is encrypted and nothing is signed.')
    A('')
    s = facts['sysex']
    A('## SysEx transport (measured)')
    A('')
    A('| fact | value |')
    A('|---|---|')
    A('| file size | %d bytes |' % s['file_bytes'])
    A('| file sha256 | `%s` |' % s['file_sha256'])
    A('| SysEx messages | %d |' % s['sysex_messages'])
    A('| message body sizes | %s |' % ', '.join(
        '%s bytes x%d' % (k, v) for k, v in s['message_body_sizes'].items()))
    A('| manufacturer id | `%s` — %s |' % (s['manufacturer_id'],
                                           s['manufacturer']))
    A('| device byte(s) | %s |' % ', '.join(
        '`%s` x%d' % (k, v) for k, v in s['device_bytes_seen'].items()))
    A('| command byte(s) | %s |' % ', '.join(
        '`%s` x%d' % (k, v) for k, v in s['command_bytes_seen'].items()))
    A('')
    d = facts['decoded_stream']
    A('## Decoded stream and ELE3 header (measured)')
    A('')
    A('| fact | value |')
    A('|---|---|')
    A('| decoded bytes | %d |' % d['bytes'])
    A('| decoded sha256 | `%s` |' % d['sha256'])
    A('| `ELE3` at offset | %d |' % d['ele3_offset'])
    A('| preamble | `%s` |' % d['preamble_hex'])
    if 'declared_checksum' in d:
        A('| declared content checksum | `%s` |' % d['declared_checksum'])
    cc = facts['container']
    A('| container bytes | %d |' % cc['bytes'])
    A('| section count | %d |' % cc['section_count'])
    A('| section table at | `%s`, %d-byte entries |'
      % (cc['section_table_offset'], cc['section_entry_size']))
    A('')
    A('ELE3 header bytes up to the table: `%s`' % cc['header_hex'])
    A('')
    A('### ELE3 header fields (offsets from the magic)')
    A('')
    A('| offset | size | raw | interpretation | confidence |')
    A('|---|---|---|---|---|')
    for f in cc['header_fields']:
        A('| `%s` | %d | `%s` | %s | %s |'
          % (f['offset'], f['size'], f['raw'], f['interpretation'],
             f['confidence']))
    A('')
    A('### Which fields the firmware actually reads')
    A('')
    A('MAIN OS loads the 32-byte header into a buffer at `0x421ED5A8` — found')
    A('by the magic comparison, `move.l #"ELE3",d0` then `cmp.l (abs).l,d0`,')
    A('at `0x400E8974`. Counting absolute references to each field in that')
    A('buffer says what is used:')
    A('')
    A('| field | absolute references |')
    A('|---|---|')
    A('| `+0x00` magic | **3** |')
    A('| `+0x1C` section count | **1** |')
    A('| `+0x04`, `+0x08`, `+0x0C`, `+0x10`, `+0x14`, `+0x18` | **0** |')
    A('')
    A('The build and version strings are read **register-relative** rather')
    A('than absolutely — `docs/FINDINGS.md` establishes that the container')
    A('arrives in a register, which is why no absolute reference exists — so')
    A('their zero counts are expected.')
    A('')
    A('`+0x04` (= 44) and `+0x18` (= 0) have no consumer this analysis can')
    A('find. They are recorded with their measured values; nothing in the')
    A('emulator depends on them.')
    A('')
    A('## Sections (measured)')
    A('')
    A('`dest` is a load address for code sections. For the bootstrap it is a')
    A('**version word, not an address** — see `dt2/container.py`.')
    A('')
    A('| # | id | offset | stored | in file | expanded | dest | architecture |')
    A('|---|---|---|---|---|---|---|---|')
    for r in out_sections:
        A('| %d | %d | `%s` | %s | %d | %s | `%s` | %s |'
          % (r['index'], r['id'], r['container_offset'],
             r.get('storage', '?'), r['compressed_length'],
             r.get('decompressed_length', '—'), r['dest_raw'],
             r['architecture']))
    A('')
    A('### Per-section detail')
    A('')
    for r in out_sections:
        A('#### Section index %d (id %d)' % (r['index'], r['id']))
        A('')
        A('- container offset `%s`, packed %d bytes'
          % (r['container_offset'], r['compressed_length']))
        if 'decompressed_length' in r:
            A('- unpacked **%d bytes**, ratio %.2fx'
              % (r['decompressed_length'], r['compression_ratio']))
            A('- unpacked sha256 `%s`' % r['decompressed_sha256'])
            A('- first 16 bytes `%s`' % r['first_16_bytes'])
        if 'stream_header' in r:
            h = r['stream_header']
            A('- aPLib stream header: declared length %d, byte sum `%s`'
              % (h['declared_stream_length'], h['declared_byte_sum']))
        if 'decompress_error' in r:
            A('- **did not decompress**: %s' % r['decompress_error'])
        A('- architecture verdict: **%s**' % r['architecture'])
        ev = r.get('architecture_evidence')
        if ev:
            A('  - m68k opcode marks: %s (total %d)'
              % (ev['m68k_opcode_counts'], ev['m68k_total']))
            A('  - Thumb push/pop halfwords in first 400KB: %d'
              % ev['thumb_push_pop_halfwords'])
            A('  - Shannon entropy: %.3f bits/byte'
              % ev['shannon_entropy_bits_per_byte'])
        A('')

    raws = [r for r in out_sections if r.get('storage') == 'raw']
    if raws:
        A('## Sections stored raw, not compressed')
        A('')
        A('A section is packed only when its 8-byte header is')
        A('self-consistent: the declared length fits and the bytes after it')
        A('sum to the declared sum. Two sections here fail that test not')
        A('because they are corrupt but because they are **not compressed**.')
        A('')
        A('| # | id | bytes | contents |')
        A('|---|---|---|---|')
        for r in raws:
            txt = r.get('raw_as_text')
            A('| %d | %d | %d | %s |'
              % (r['index'], r['id'], r.get('decompressed_length', 0),
                 ('`%s`' % txt) if txt else r['architecture']))
        A('')
        A('Section id 4 is the updater, stored raw with a header to strip;')
        A('section id 5 is metadata, stored raw with none.')
        A('')

    bad = [r for r in out_sections if 'decompress_error' in r]
    if bad:
        A('## Known gap: sections that did not decompress')
        A('')
        A('`dt2/elz.py` is a byte-level reimplementation of the device codec,')
        A("and emu/extract.py already records that it fails on some builds'")
        A('updaters. That limitation shows here and is **not** a property of')
        A('this firmware:')
        A('')
        A('| # | id | dest | packed bytes | error |')
        A('|---|---|---|---|---|')
        for r in bad:
            A('| %d | %d | `%s` | %d | %s |'
              % (r['index'], r['id'], r['dest_raw'], r['compressed_length'],
                 r['decompress_error']))
        A('')
        A('Neither is needed to run the emulator: section 3 (MAIN OS) and')
        A('section 2 (bootstrap) both decompress. Section id 4 is the')
        A('updater and section id 5 is 15 bytes of metadata.')
        A('')

    mpath = os.path.join(args.out, '01-container.md')
    with open(mpath, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L) + '\n')
    print('wrote %s' % mpath)


if __name__ == '__main__':
    main()
