#!/usr/bin/env python3
"""Consolidate every open question from the generated fact files.

The point of documenting bottom-up is to know where the edge of the knowledge
is. This reads docs/mk1/facts/*.json and collects everything those documents
marked unknown, so the open list is generated from the same measurements as
the answers and cannot drift away from them.

Run it last, after the other mk1doc tools.

    python tools/mk1doc_unknowns.py --out docs/mk1
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load(out, name):
    path = os.path.join(out, 'facts', name)
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='docs/mk1')
    args = ap.parse_args()

    container = load(args.out, 'container.json')
    mmio = load(args.out, 'mmio.json')
    syms = load(args.out, 'symbols.json')
    vecs = load(args.out, 'vectors.json')

    items = []

    if container:
        for f in container.get('container', {}).get('header_fields', []):
            if 'MEANING UNKNOWN' in f.get('confidence', ''):
                items.append({
                    'area': 'container',
                    'item': 'ELE3 header field at %s' % f['offset'],
                    'known': 'raw bytes %s (%s)' % (f['raw'],
                                                    f['interpretation']),
                    'unknown': 'what the field means',
                    'how_to_resolve': 'compare the same offset across other '
                                      'Elektron firmware releases; a field '
                                      'that tracks version or size will show '
                                      'it',
                })
        for s in container.get('sections', []):
            if 'decompress_error' in s:
                items.append({
                    'area': 'container',
                    'item': 'section id %s does not decompress' % s['id'],
                    'known': '%d packed bytes at %s, dest %s'
                             % (s['compressed_length'],
                                s['container_offset'], s['dest_raw']),
                    'unknown': 'its contents',
                    'how_to_resolve': 'dt2/elz.py is a reimplementation and '
                                      'emu/extract.py notes it fails on some '
                                      'updaters; use the device\'s own '
                                      'depacker (emu/extract.py depack) '
                                      'instead. Not needed to run the '
                                      'emulator.',
                })

    if mmio:
        for b in mmio.get('blocks', []):
            if b['name'] == 'UNIDENTIFIED' and b['distinct_offsets'] >= 2 \
                    and not b['base'].startswith('0x8000'):
                items.append({
                    'area': 'hardware',
                    'item': 'unidentified peripheral block %s' % b['base'],
                    'known': '%d opcode-anchored references across %d '
                             'distinct register offsets'
                             % (b['references'], b['distinct_offsets']),
                    'unknown': 'which peripheral it is',
                    'how_to_resolve': 'look it up in the MCF5441x reference '
                                      'manual register map by base address, '
                                      'then add it to the contract',
                })
        for b in mmio.get('referenced_but_not_modelled', []):
            if b['name'] != 'UNIDENTIFIED':
                items.append({
                    'area': 'emulation gap',
                    'item': '%s (%s) is referenced but not modelled'
                            % (b['name'], b['base']),
                    'known': '%d references across %d distinct offsets'
                             % (b['references'], b['distinct_offsets']),
                    'unknown': 'what the firmware expects back from it, and '
                               'whether its absence changes behaviour',
                    'how_to_resolve': 'hook reads of the block and see what '
                                      'the firmware does with the value',
                })

    if syms:
        for s in syms.get('symbols', []):
            if s['value'] is None:
                items.append({
                    'area': 'symbols',
                    'item': 'symbol %s unresolved' % s['name'],
                    'known': 'rule %s; %s' % (s['rule_type'],
                                              s['resolver_detail'] or 'no '
                                              'detail recorded'),
                    'unknown': 'its address in this image',
                    'how_to_resolve': ('re-derive from this image: the Fixed '
                                       'address is a Digitakt II one and its '
                                       'verify bytes do not match'
                                       if s['rule_type'] == 'Fixed' else
                                       'find the mk1 equivalent of the '
                                       'signature, which matches 0 times '
                                       'here'),
                })

    if vecs:
        for v in vecs.get('vectors', []):
            if v['is_default_stub'] or v['vector'] < 64:
                continue
            # A vector is only unknown if NOTHING names it: neither the
            # contract (peripheral) nor the manual's INTC source tables
            # (intc_module). A source the manual lists as an unused gap IS
            # named -- it is a software-forced interrupt, which is knowledge,
            # not a hole.
            if v.get('peripheral') or v.get('intc_module'):
                continue
            items.append({
                'area': 'interrupts',
                'item': 'vector %d (%s source %s) -> %s'
                        % (v['vector'], v['class'], v['intc_source'],
                           v['handler']),
                'known': 'a real handler is installed, so the firmware '
                         'expects this interrupt',
                'unknown': 'which peripheral raises it',
                'how_to_resolve': 'map the INTC source number in the '
                                  'MCF5441x reference manual interrupt '
                                  'source table',
            })

    by_area = {}
    for it in items:
        by_area.setdefault(it['area'], []).append(it)

    facts = {
        'document': 'digitakt-mk1.unknowns',
        'schema_version': 1,
        'note': 'Generated from the other fact files. Every entry is '
                'something a generated document marked unknown, so this list '
                'cannot drift from the measurements behind it.',
        'total': len(items),
        'by_area': {k: len(v) for k, v in by_area.items()},
        'items': items,
    }
    jpath = os.path.join(args.out, 'facts', 'unknowns.json')
    with open(jpath, 'w', encoding='utf-8') as fh:
        json.dump(facts, fh, indent=2)
        fh.write('\n')
    print('wrote %s' % jpath)

    L = []
    A = L.append
    A('# 09 — What is still unknown')
    A('')
    A('Generated by `tools/mk1doc_unknowns.py` from the other fact files, so')
    A('it cannot drift away from the measurements behind it.')
    A('Machine-readable: `facts/unknowns.json`.')
    A('')
    A('**%d open items**: %s' % (len(items), ', '.join(
        '%s %d' % (k, len(v)) for k, v in sorted(by_area.items()))))
    A('')
    A('Each says what IS known, what is not, and how to resolve it — an entry')
    A('here is a piece of work, not a shrug.')
    A('')
    for area in sorted(by_area):
        A('## %s' % area)
        A('')
        for it in by_area[area]:
            A('### %s' % it['item'])
            A('')
            A('- **known:** %s' % it['known'])
            A('- **unknown:** %s' % it['unknown'])
            A('- **how to resolve:** %s' % it['how_to_resolve'])
            A('')

    mpath = os.path.join(args.out, '09-unknowns.md')
    with open(mpath, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L) + '\n')
    print('wrote %s (%d items)' % (mpath, len(items)))


if __name__ == '__main__':
    main()
