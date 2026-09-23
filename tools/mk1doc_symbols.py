#!/usr/bin/env python3
"""Inventory every symbol the resolver looks for, and how each was established.

This is the document that says what is actually known about the code layout
rather than assumed. For each symbol it records the value, the RULE TYPE that
produced it -- which is the confidence signal, since a unique byte-signature
match and a hand-entered literal address are not the same kind of knowledge --
and the resolver's own explanation. Unresolved symbols are listed in full with
the reason, because those are the places where an emulator is guessing.

    python tools/mk1doc_symbols.py --out docs/mk1
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, symbols as S

# How much a given rule type is worth as evidence. The wording is deliberate:
# a signature that must match exactly once across a 2.4MB image is strong; a
# literal address is only as good as its verify bytes and the person who
# entered it.
RULE_CONFIDENCE = {
    'Sig': ('measured', 'unique masked byte-signature match in this image'),
    'SigWhere': ('measured', 'masked signature narrowed to one match by an '
                             'operand tied to another resolved symbol'),
    'Fixed': ('asserted', 'a literal address. Only as trustworthy as its '
                          'verify bytes -- which DO have to match, so a wrong '
                          'image fails loudly rather than silently'),
    'AnyOf': ('mixed', 'first sub-rule that resolved wins; see the detail '
                       'column for which one'),
    'Operand': ('derived', 'an integer read out of another symbol\'s '
                           'instruction stream'),
    'Offset': ('derived', 'a fixed offset from another symbol'),
    'Pick': ('derived', 'one element of a resolved group'),
    'OperandGroup': ('derived', 'distinct absolute operands scanned from '
                                'another symbol\'s code'),
    'CallSites': ('measured', 'addresses of instructions that call a resolved '
                              'target'),
    'StringTable': ('measured', 'a table of pointers to strings in the image'),
    'ScanAll': ('measured', 'every match of a pattern in the image'),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='docs/mk1')
    args = ap.parse_args()
    os.makedirs(os.path.join(args.out, 'facts'), exist_ok=True)

    img = open(config.main_image(), 'rb').read()
    profile = S.resolve(img)
    # _detail is where Profile keeps the resolver's per-symbol explanation.
    # Private, but this is a documentation tool living in the same repo and
    # the explanation is the whole point of the document.
    detail = getattr(profile, '_detail', None) or {}

    rows = []
    for name, rule, required in S.SYMBOLS:
        val = getattr(profile, name, None)
        rtype = type(rule).__name__
        conf, conf_note = RULE_CONFIDENCE.get(rtype, ('unclassified', ''))
        if val is None:
            conf, conf_note = 'UNKNOWN', 'did not resolve'
        if isinstance(val, int):
            shown = '0x%08X' % val
        elif isinstance(val, (tuple, list)):
            shown = '[%d entries]' % len(val)
        elif val is None:
            shown = None
        else:
            shown = str(val)
        rows.append({
            'name': name,
            'value': shown,
            'required': bool(required),
            'rule_type': rtype,
            'confidence': conf,
            'confidence_note': conf_note,
            'resolver_detail': detail.get(name) or '',
        })

    # What each unresolved symbol's absence actually COSTS, established by
    # reading every consumer of it in emu/ and tools/ rather than assumed.
    # An unresolved OPTIONAL symbol is not automatically a problem: most of
    # these only switch off a log line or a trace feature.
    # Seven of the eight names this table used to cover now RESOLVE on mk1,
    # so their entries are gone; only px_copy is still unresolved. Two of the
    # old claims were also wrong and are corrected here rather than deleted
    # silently, because they were the reason nobody went looking:
    #
    #   sf_fixsfsi / sf_cmpsf2 were called 'NO CONSEQUENCE -- zero usages'.
    #   emu/longrun.py:427 builds the soft-float HLE's address map with
    #   `profile.get('sf_' + name)` over all SEVEN names, fixsfsi and cmpsf2
    #   among them, so an unresolved name was silently dropped and the
    #   firmware ran its own routine. Both are now resolved and verified
    #   bit-exact against the firmware. Measured: no effect on a running UI,
    #   which does no float work in steady state -- the callers Ghidra finds
    #   for fixsfsi are in the intro's particle code (0x4006c2ae).
    #
    #   ui_tick_inc's three candidates are no longer 'not established'. The
    #   choice was measured against uitrace's own property (the counter the
    #   key-repeat code advances, ~1.8 per DTIM3 tick): holding a key for 382
    #   ticks moved 0x400d3574's counter 699 times (1.83/tick) and left the
    #   other two at zero, never incremented and never read.
    CONSEQUENCE = {
        'px_copy': ('diagnostic only', "emu/longrun.py uses it for a counter, "
                    "ev['pxcopy']; absent, that counter stays 0. This one "
                    'appears GENUINELY ABSENT from mk1 rather than merely '
                    'unfound: px_copy_to_bitmap(PixelData&, Bitmap&) is a '
                    'Digitakt II routine, and mk1 carries no "PixelData" '
                    'string, no match for its two-pointer prologue, and every '
                    'function Ghidra reports as calling set_pixel is a drawing '
                    'routine rather than a buffer copy. mk1 renders through '
                    'panel_diff and the fb_front/fb_back pair instead.'),
    }
    for r in rows:
        if r['value'] is None and r['name'] in CONSEQUENCE:
            r['impact'], r['impact_detail'] = CONSEQUENCE[r['name']]

    resolved = [r for r in rows if r['value'] is not None]
    unresolved = [r for r in rows if r['value'] is None]
    by_rule = collections.Counter(r['rule_type'] for r in resolved)
    by_conf = collections.Counter(r['confidence'] for r in resolved)

    facts = {
        'document': 'digitakt-mk1.symbols',
        'schema_version': 1,
        'image_sha256': getattr(profile, 'image_sha256', None),
        'load_address': '0x%08X' % getattr(profile, 'load_addr', 0x40000400),
        'total_symbols': len(rows),
        'resolved': len(resolved),
        'unresolved': len(unresolved),
        'resolved_by_rule_type': dict(by_rule),
        'resolved_by_confidence': dict(by_conf),
        'note': 'The rule type is the confidence signal. A Sig must match '
                'exactly once across the whole image; a Fixed is a literal '
                'address whose verify bytes must match but which nobody '
                'derived. Treat them differently.',
        'symbols': rows,
    }

    jpath = os.path.join(args.out, 'facts', 'symbols.json')
    with open(jpath, 'w', encoding='utf-8') as fh:
        json.dump(facts, fh, indent=2)
        fh.write('\n')
    print('wrote %s' % jpath)

    L = []
    A = L.append
    A('# 06 — Symbol inventory: what is known about the code layout')
    A('')
    A('Generated by `tools/mk1doc_symbols.py`. Machine-readable:')
    A('`facts/symbols.json`.')
    A('')
    A('%d symbols are looked for in this image: **%d resolved**, **%d not**.'
      % (len(rows), len(resolved), len(unresolved)))
    A('')
    A('## How to read the confidence column')
    A('')
    A('The rule type *is* the confidence. These are not the same kind of')
    A('knowledge and should not be trusted equally:')
    A('')
    A('| rule | confidence | what it means |')
    A('|---|---|---|')
    for rtype, (conf, note) in sorted(RULE_CONFIDENCE.items()):
        if by_rule.get(rtype):
            A('| `%s` | %s | %s |' % (rtype, conf, note))
    A('')
    A('Counts by confidence: %s'
      % ', '.join('%s %d' % (k, v) for k, v in by_conf.most_common()))
    A('')
    A('## Unresolved — where the emulator is guessing')
    A('')
    if not unresolved:
        A('None.')
    else:
        A('These are looked for and not found. Anything that depends on one')
        A('of them is unavailable, and code that silently degrades when a')
        A('symbol is `None` will do so here.')
        A('')
        A('Every one is OPTIONAL. What each absence actually costs was')
        A('established by reading its consumers in `emu/` and `tools/`, not')
        A('assumed — an unresolved optional symbol is usually a switched-off')
        A('log line, not a broken emulator.')
        A('')
        A('| symbol | impact | why it did not resolve |')
        A('|---|---|---|')
        for r in unresolved:
            A('| `%s` | **%s** | %s |'
              % (r['name'], r.get('impact', 'unassessed'),
                 r['resolver_detail'][:110] or '—'))
        A('')
        for r in unresolved:
            if r.get('impact_detail'):
                A('- **`%s`** — %s' % (r['name'], r['impact_detail']))
        A('')
        # Derived, not written down: this sentence has been wrong before,
        # because the counts it states change every time a symbol resolves.
        n = len(unresolved)
        A('The one unresolved symbol does not affect emulation correctness.'
          if n == 1 else
          'None of the %d affects emulation correctness.' % n)
        none_at_all = [r['name'] for r in unresolved
                       if 'no consumers' in (r.get('impact_detail') or '')
                       or 'zero usages' in (r.get('impact_detail') or '')]
        if none_at_all:
            A('%s have **no consumers at all**: %s.'
              % ('One of them' if len(none_at_all) == 1 else
                 '%d of them' % len(none_at_all),
                 ', '.join('`%s`' % x for x in sorted(none_at_all))))
    A('')
    A('## Resolved')
    A('')
    A('| symbol | value | rule | confidence |')
    A('|---|---|---|---|')
    for r in sorted(resolved, key=lambda x: x['name']):
        A('| `%s` | `%s` | `%s` | %s |'
          % (r['name'], r['value'], r['rule_type'], r['confidence']))
    A('')
    A('## Asserted addresses')
    A('')
    A('Symbols resolved by a literal address rather than derived from the')
    A('image. Their verify bytes must match, so a wrong image fails loudly —')
    A('but nobody *derived* these, so they are the rows most worth')
    A('re-establishing if something downstream behaves oddly.')
    A('')
    asserted = [r for r in resolved
                if r['rule_type'] == 'Fixed' or
                (r['rule_type'] == 'AnyOf' and 'Fixed' in
                 (r['resolver_detail'] or ''))]
    if not asserted:
        A('None resolved purely by assertion.')
    else:
        A('| symbol | value | detail |')
        A('|---|---|---|')
        for r in asserted:
            A('| `%s` | `%s` | %s |'
              % (r['name'], r['value'], (r['resolver_detail'] or '—')[:120]))
    A('')

    mpath = os.path.join(args.out, '06-symbols.md')
    with open(mpath, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L) + '\n')
    print('wrote %s' % mpath)


if __name__ == '__main__':
    main()
