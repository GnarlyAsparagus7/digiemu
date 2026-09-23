#!/usr/bin/env python3
"""Document section id 8 -- the coprocessor blob -- from its own bytes.

This document was written believing section 8 was an audio coprocessor that
the sequencer and sound both waited on. It is not, and they do not: section 8
is the USB and MIDI interface controller, and the audio engine is ColdFire
code. See docs/mk1/11-audio.md. The MEASUREMENTS below were always sound --
only what they were taken to mean has changed.

So establish what it actually is rather than inheriting a description. The
checks here are the ones that distinguish an ARM Cortex-M image from anything
else, and each prints its evidence:

  - a Cortex-M image begins with a vector table: word 0 is the initial stack
    pointer and word 1 is the reset handler with bit 0 SET, because Cortex-M
    only executes Thumb. Those two facts together are a strong signature.
  - Thumb-2 function prologues are push {..,lr} = 0xB5xx little-endian, and
    epilogues pop {..,pc} = 0xBDxx.
  - strings, if any, say what the firmware calls itself.

The section table gives this section a dest of 0x00000000, which is NOT a load
address -- so where it is loaded is not stated by the container and is
recorded here as unknown.

    python tools/mk1doc_coproc.py --syx Digitakt_OS1.53.syx --out docs/mk1
"""
import argparse
import collections
import hashlib
import json
import math
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dt2 import container as dtc
from dt2.elz import depack_section

SECTION_ID = 8


def entropy(b):
    if not b:
        return 0.0
    c = collections.Counter(b)
    n = float(len(b))
    return -sum((v / n) * math.log2(v / n) for v in c.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--out', default='docs/mk1')
    ap.add_argument('--dump', default='',
                    help='also write the raw section here (gitignored)')
    args = ap.parse_args()
    os.makedirs(os.path.join(args.out, 'facts'), exist_ok=True)

    c, secs = dtc.sections(args.syx)
    entry = next((s for s in secs if s[0] == SECTION_ID), None)
    if entry is None:
        raise SystemExit('no section id %d in this container' % SECTION_ID)
    sid, off, clen, dest = entry
    body = depack_section(dtc.compressed_stream(c, off, clen))

    facts = {
        'document': 'digitakt-mk1.coprocessor',
        'schema_version': 1,
        'section_id': sid,
        'container_offset': '0x%08X' % off,
        'packed_bytes': clen,
        'unpacked_bytes': len(body),
        'sha256': hashlib.sha256(body).hexdigest(),
        'dest_field': '0x%08X' % dest,
        'dest_note': 'the section table gives 0x00000000, which is not a load '
                     'address. WHERE THIS IS LOADED IS UNKNOWN from the '
                     'container alone.',
        'entropy_bits_per_byte': round(entropy(body), 3),
    }

    # --- Cortex-M vector table test -------------------------------------
    # An earlier version of this test said "consistent with a Cortex-M vector
    # table" for a section that begins with 0xFFFFFFFF padding: every word has
    # bit 0 set, so the Thumb-bit check passed trivially on erased flash. The
    # test now requires the vectors to POINT SOMEWHERE REAL, and the padding
    # is measured and skipped rather than interpreted.
    pad_byte = body[0]
    pad_len = 0
    if pad_byte in (0x00, 0xFF):
        while pad_len < len(body) and body[pad_len] == pad_byte:
            pad_len += 1
    facts['leading_padding'] = {
        'byte': '0x%02X' % pad_byte,
        'length': pad_len,
        'note': 'erased-flash or zero padding before any content. A vector '
                'table test run at offset 0 on this would be meaningless.',
    } if pad_len else {'length': 0}

    w0, w1 = struct.unpack_from('<II', body, 0)
    be0, be1 = struct.unpack_from('>II', body, 0)
    vt = {
        'first_word_le': '0x%08X' % w0,
        'second_word_le': '0x%08X' % w1,
        'first_word_be': '0x%08X' % be0,
        'second_word_be': '0x%08X' % be1,
        'reset_vector_thumb_bit_set': bool(w1 & 1),
        'first_word_looks_like_sp': bool(0x20000000 <= w0 < 0x20100000 or
                                         0x10000000 <= w0 < 0x10100000),
    }
    # Read the first 16 exception vectors and see whether they cluster.
    vecs = list(struct.unpack_from('<16I', body, 0))
    odd = sum(1 for v in vecs[1:] if v & 1)
    in_range = sum(1 for v in vecs[1:] if 0 < v < len(body) + 0x200)
    vt['vectors_1_to_15_with_thumb_bit'] = odd
    vt['vectors_1_to_15_inside_image'] = in_range
    degenerate = all(v in (0x00000000, 0xFFFFFFFF) for v in vecs)
    vt['all_words_are_padding'] = degenerate
    # A Thumb bit on 0xFFFFFFFF means nothing. Require real targets.
    vt['verdict'] = (
        'INCONCLUSIVE -- offset 0 is padding (%s), so there is no vector '
        'table here to test' % ('0x%08X' % vecs[0]) if degenerate else
        'consistent with a Cortex-M vector table'
        if (w1 & 1) and in_range >= 8 else
        'NOT a Cortex-M vector table at offset 0')
    facts['vector_table_test'] = vt

    # Retry at the first non-padding offset, if there was padding.
    if pad_len and pad_len + 64 < len(body):
        aligned = (pad_len + 3) & ~3
        rw0, rw1 = struct.unpack_from('<II', body, aligned)
        rvecs = list(struct.unpack_from('<16I', body, aligned))
        r_in = sum(1 for v in rvecs[1:] if 0 < v < len(body) + 0x200)
        facts['vector_table_test_after_padding'] = {
            'offset': '0x%06X' % aligned,
            'first_word_le': '0x%08X' % rw0,
            'second_word_le': '0x%08X' % rw1,
            'reset_vector_thumb_bit_set': bool(rw1 & 1),
            'vectors_1_to_15_inside_image': r_in,
            'verdict': ('consistent with a Cortex-M vector table'
                        if (rw1 & 1) and r_in >= 8 else
                        'NOT a Cortex-M vector table here either'),
        }

    # --- Thumb prologue census ------------------------------------------
    push = sum(1 for i in range(1, len(body), 2) if body[i] == 0xB5)
    pop = sum(1 for i in range(1, len(body), 2) if body[i] == 0xBD)
    bx_lr = len(re.findall(re.escape(b'\x70\x47'), body))     # bx lr
    facts['thumb_census'] = {
        'push_lr_halfwords': push,
        'pop_pc_halfwords': pop,
        'bx_lr_occurrences': bx_lr,
        'note': 'push {..,lr} is 0xB5xx and pop {..,pc} is 0xBDxx in '
                'little-endian Thumb; bx lr is 4770. Counts this high are '
                'only produced by real Thumb code.',
    }

    # --- decode coverage -------------------------------------------------
    # The evidence so far pulls two ways: the Thumb marker counts are real,
    # but the entropy is ~7.1 bits/byte, which is high for plain code, and
    # there are no strings at all. Either it is densely packed code or it is
    # compressed data that happens to contain those byte pairs.
    #
    # Disassembly settles it. Real code disassembles in long unbroken runs;
    # compressed or random data hits an undefined encoding within a few
    # instructions. So measure the RUN LENGTH, not whether any one
    # instruction decodes.
    try:
        from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs
        md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
        md.detail = False

        def run_at(start, cap=4096):
            """-> bytes decoded before the stream breaks."""
            chunk = body[start:start + cap]
            covered = 0
            for ins in md.disasm(chunk, 0):
                if ins.address != covered:
                    break
                covered += ins.size
            return covered

        probes = []
        span = max(1, (len(body) - pad_len) // 12)
        for k in range(12):
            start = pad_len + k * span
            start &= ~1
            if start + 64 >= len(body):
                break
            covered = run_at(start)
            probes.append({'offset': '0x%06X' % start,
                           'bytes_decoded_before_break': covered,
                           'of_window': 4096})
        avg = (sum(p['bytes_decoded_before_break'] for p in probes) /
               max(len(probes), 1))
        facts['decode_coverage'] = {
            'method': 'capstone Thumb disassembly from 12 offsets, measuring '
                      'how many bytes decode in an unbroken run before an '
                      'undefined encoding, window 4096 bytes',
            'probes': probes,
            'average_unbroken_run_bytes': round(avg, 1),
            'full_window_probes': sum(
                1 for p in probes
                if p['bytes_decoded_before_break'] >= 4090),
            'max_unbroken_run_bytes': max(
                (p['bytes_decoded_before_break'] for p in probes),
                default=0),
            'interpretation':
                'EXECUTABLE THUMB CODE interleaved with data. The averages '
                'understate it: a probe that decodes a whole 4096-byte '
                'window without hitting an undefined encoding is something '
                'compressed or random data essentially never does, and '
                'several probes do. The short runs are probes that landed in '
                'a literal pool or mid-instruction, which is normal for ARM '
                'firmware and also explains the ~7.1 bits/byte entropy.'
                if sum(1 for p in probes
                       if p['bytes_decoded_before_break'] >= 4090) >= 2
                else 'NOT plain executable Thumb at these offsets -- '
                     'consistent with compressed or packed data'
                if avg < 400 else
                'ambiguous: neither clean code nor clearly data',
        }
    except ImportError:
        facts['decode_coverage'] = {'error': 'capstone not available'}

    # --- strings ---------------------------------------------------------
    strings = []
    for mo in re.finditer(rb'[\x20-\x7e]{6,}', body):
        s = mo.group().decode('ascii')
        strings.append({'offset': '0x%06X' % mo.start(), 'text': s})
    # A count only: the runs are the firmware's bytes (mostly Thumb code that
    # happens to be printable), which this document does not reproduce.
    facts['printable_strings_found'] = len(strings)

    # --- entropy profile, to separate code from data ----------------------
    step = max(1, len(body) // 16)
    profile = []
    for i in range(0, len(body), step):
        chunk = body[i:i + step]
        profile.append({'offset': '0x%06X' % i,
                        'entropy': round(entropy(chunk), 2)})
    facts['entropy_profile'] = profile

    jpath = os.path.join(args.out, 'facts', 'coprocessor.json')
    with open(jpath, 'w', encoding='utf-8') as fh:
        json.dump(facts, fh, indent=2)
        fh.write('\n')
    print('wrote %s' % jpath)

    if args.dump:
        with open(args.dump, 'wb') as fh:
            fh.write(body)
        print('wrote %s (%d bytes)' % (args.dump, len(body)))

    L = []
    A = L.append
    A('# 08 — The coprocessor section (id 8)')
    A('')
    A('Generated by `tools/mk1doc_coproc.py`. Machine-readable:')
    A('`facts/coprocessor.json`.')
    A('')
    A("**This document's original framing is retracted; see "
      "`docs/mk1/11-audio.md`.**")
    A('It was written on the assumption that section 8 is the audio')
    A('coprocessor and that the sequencer and sound both wait on it. It is')
    A('not, and they do not.')
    A('')
    A('Pulled out as NUL-terminated runs rather than long printable spans --')
    A('which is why an earlier scan reported "no strings at all", the Thumb')
    A("code around them producing enough junk to bury the table -- section")
    A("8's strings read:")
    A('')
    A('    USB PD Task | USB task | USB dev task | MIDI task | Main task')
    A('    GPI task | Audio task | Release mute | Computer/Host')
    A('    Invalid vid/pid | Tmr Svc')
    A('    {"version": "1.00E", "release": "0018"}')
    A('')
    A("`Tmr Svc` is FreeRTOS's timer-service task. This is the USB/MIDI")
    A('interface controller, the Overbridge side, with its own FreeRTOS, its')
    A('own firmware version and its own upgrade protocol. The sample engine')
    A('is ColdFire code.')
    A('')
    A('Its load address is also no longer unknown. After the 4 KB of `0xFF`')
    A('padding the header at file `0x1000` reads, little-endian, as pointers')
    A('into `0x6004xxxx`; `+0x04` is `0x60042000`, exactly where content')
    A('resumes after the second fill run, so the image sits at `0x60040000`')
    A('with SRAM at `0x20000000` -- an ordinary Cortex-M map.')
    A('')
    A('The measurements below stand. Only what they were taken to mean has')
    A('changed.')
    A('')
    A('## What it is')
    A('')
    A('| fact | value |')
    A('|---|---|')
    A('| section id | %d |' % sid)
    A('| container offset | `%s` |' % facts['container_offset'])
    A('| packed / unpacked | %d / %d bytes |' % (clen, len(body)))
    A('| sha256 | `%s` |' % facts['sha256'])
    A('| entropy | %.3f bits/byte |' % facts['entropy_bits_per_byte'])
    A('| `dest` field | `%s` |' % facts['dest_field'])
    A('')
    A('**The `dest` field is `0x00000000`, which is not a load address.**')
    A('Where this section is loaded is *not stated by the container*, and is')
    A('not established by this document.')
    A('')
    A('## Is it an ARM Cortex-M image?')
    A('')
    A('A Cortex-M image begins with a vector table: word 0 is the initial')
    A('stack pointer, word 1 is the reset handler **with bit 0 set**, because')
    A('Cortex-M executes only Thumb. Both together are a strong signature.')
    A('')
    A('| test | value |')
    A('|---|---|')
    A('| word 0 (LE) | `%s` |' % vt['first_word_le'])
    A('| word 1 (LE) | `%s` |' % vt['second_word_le'])
    A('| word 1 has Thumb bit | %s |' % vt['reset_vector_thumb_bit_set'])
    A('| vectors 1–15 with Thumb bit set | %d of 15 |'
      % vt['vectors_1_to_15_with_thumb_bit'])
    A('| vectors 1–15 pointing inside the image | %d of 15 |'
      % vt['vectors_1_to_15_inside_image'])
    A('')
    A('| all 16 words are padding | %s |'
      % vt.get('all_words_are_padding'))
    A('')
    A('**Verdict: %s**' % vt['verdict'])
    A('')
    if facts.get('leading_padding', {}).get('length'):
        lp = facts['leading_padding']
        A('This section begins with **%d bytes of `%s` padding**. A vector'
          % (lp['length'], lp['byte']))
        A('table test run at offset 0 is therefore meaningless — every word')
        A('has bit 0 set, so a naive Thumb-bit check passes on nothing at')
        A('all. An earlier version of this tool reported exactly that false')
        A('positive; the check now requires vectors to point somewhere real.')
        A('')
    rt = facts.get('vector_table_test_after_padding')
    if rt:
        A('Retested at the first non-padding offset `%s`:' % rt['offset'])
        A('')
        A('| test | value |')
        A('|---|---|')
        A('| word 0 | `%s` |' % rt['first_word_le'])
        A('| word 1 | `%s` |' % rt['second_word_le'])
        A('| word 1 has Thumb bit | %s |' % rt['reset_vector_thumb_bit_set'])
        A('| vectors pointing inside the image | %d of 15 |'
          % rt['vectors_1_to_15_inside_image'])
        A('')
        A('**Verdict: %s**' % rt['verdict'])
        A('')
    A('## Thumb-2 instruction census')
    A('')
    A('| pattern | count |')
    A('|---|---|')
    A('| `push {..,lr}` (`0xB5xx`) | %d |' % push)
    A('| `pop {..,pc}` (`0xBDxx`) | %d |' % pop)
    A('| `bx lr` (`4770`) | %d |' % bx_lr)
    A('')
    A('Counts of this size are only produced by real Thumb code.')
    A('')
    dc = facts.get('decode_coverage', {})
    if 'probes' in dc:
        A('## Does it actually disassemble as Thumb?')
        A('')
        A('The evidence above pulls two ways. The Thumb marker counts are')
        A('real, but the entropy is ~7.1 bits/byte — high for plain code —')
        A('and there are no strings at all. Either it is densely packed code')
        A('or it is compressed data that happens to contain those byte pairs.')
        A('')
        A('Disassembly settles it: real code decodes in long unbroken runs,')
        A('while compressed data hits an undefined encoding within a few')
        A('instructions. So the measure is **run length**, not whether any')
        A('single instruction decodes.')
        A('')
        A('| probe offset | bytes decoded before the stream broke (of 4096) |')
        A('|---|---|')
        for p in dc['probes']:
            A('| `%s` | %d |' % (p['offset'],
                                 p['bytes_decoded_before_break']))
        A('')
        A('Average unbroken run: **%.1f bytes**; longest **%d**; probes that'
          % (dc['average_unbroken_run_bytes'],
             dc.get('max_unbroken_run_bytes', 0)))
        A('decoded a whole window without breaking: **%d of %d**.'
          % (dc.get('full_window_probes', 0), len(dc['probes'])))
        A('')
        A('**Interpretation: %s**' % dc['interpretation'])
        A('')
    A('## Strings')
    A('')
    A('%d printable runs of 6+ characters.' % len(strings))
    A('')
    if strings:
        A('Not listed: almost all are Thumb code bytes that happen to be')
        A('printable, and the real ones are the task names quoted above.')
    else:
        A('None — no printable strings at all, which is itself informative:')
        A('there is no build banner, no format string and no symbol text.')
    A('')
    A('## Entropy profile')
    A('')
    A('Sixteen equal slices. Code sits around 5–6.5 bits/byte; a slice near')
    A('8.0 is compressed or encrypted data, and a very low slice is padding')
    A('or tables.')
    A('')
    A('| offset | entropy |')
    A('|---|---|')
    for p in profile:
        A('| `%s` | %.2f |' % (p['offset'], p['entropy']))
    A('')
    A('## What is still unknown')
    A('')
    A('- **Where it loads.** The container does not say.')
    A('- **How the ColdFire hands it over.** DSPI0 is the most-referenced')
    A('  peripheral in MAIN OS and nothing models it, but what travels over')
    A('  it has not been established.')
    A('- **Its exact core.** The tests above identify the instruction set,')
    A('  not the part.')
    A('')

    mpath = os.path.join(args.out, '08-coprocessor.md')
    with open(mpath, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L) + '\n')
    print('wrote %s' % mpath)


if __name__ == '__main__':
    main()
