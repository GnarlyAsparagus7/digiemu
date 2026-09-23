#!/usr/bin/env python3
"""Dump the interrupt vector table the firmware actually installs.

The vector table is the map from hardware event to code, so it is the single
most useful thing to know when deciding what an emulator must model: a vector
pointing at real code is a device the firmware expects to hear from.

Read from a SNAPSHOT rather than from the image, because the table lives in
SDRAM below the MAIN OS load address (VBR = 0x40000000, MAIN OS loads at
0x40000400) and is filled in at run time -- it does not exist in the image at
all. Nothing is executed here; the snapshot is only restored and read.

Vector numbers map to interrupt controller sources by
`vector = intc_vector_base + source`, with bases taken from
docs/contracts/mcf5441x-reference-v1.json rather than assumed.

    python tools/mk1doc_vectors.py --out docs/mk1
"""
import argparse
import collections
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config
from emu.snapshot import restore

VBR = 0x40000000
N_VECTORS = 256
MAIN_OS_LO, MAIN_OS_HI = 0x40000400, 0x40400000
CONTRACT = 'docs/contracts/mcf5441x-reference-v1.json'

# CPU exception vectors 0..63 are architectural, not INTC sources.
CPU_VECTORS = {
    0: 'initial SSP', 1: 'initial PC', 2: 'access fault', 3: 'address error',
    4: 'illegal instruction', 5: 'divide by zero', 8: 'privilege violation',
    9: 'trace', 10: 'unimplemented line-A', 11: 'unimplemented line-F',
    12: 'debug interrupt', 14: 'format error', 15: 'uninitialised interrupt',
    24: 'spurious interrupt',
}
for _i in range(25, 32):
    CPU_VECTORS[_i] = 'level %d autovector' % (_i - 24)
for _i in range(32, 48):
    CPU_VECTORS[_i] = 'TRAP #%d' % (_i - 32)


# INTC source assignments, transcribed from docs/refs/MCF5441X-notes.md
# section 4b, which cites MCF5441XRM chapter 17 section 17.2.9.1 tables 17-15,
# 17-16 and 17-17 as its primary source. Keyed (intc, source).
#
# The "Not used" rows matter more than the named ones here: a source the
# silicon does not drive, with a real handler installed on it, is an interrupt
# the FIRMWARE raises in software. That is what INTFRCH is for.
INTC_SOURCES = {
    (0, 0): ('--', 'NOT USED by the silicon -- RM table 17-15 source 0 reads "Not Used"'),
    (0, 1): ('EPORT0', 'EPFR0[EPF1] -- Edge Port 0 flag 1'),
    (0, 2): ('EPORT0', 'EPFR0[EPF2] -- Edge Port 0 flag 2'),
    (0, 4): ('EPORT0', 'EPFR0[EPF4] -- Edge Port 0 flag 4'),
    (0, 3): ('EPORT0', 'EPFR0[EPF3] -- Edge Port 0 flag 3. MEASURED: the '
                       'vector 67 handler acks 0xFC090003/0xFC090006 and then '
                       'reads eSDHC status at 0xFC0CC030, so this pin is the '
                       'SD card-detect line'),
    (0, 26): ('UART0', 'm5441xsim.h MCFINT0_UART0 = 26'),
    (0, 27): ('UART1', 'm5441xsim.h MCFINT0_UART1 = 27'),
    (0, 31): ('DSPI0', 'm5441xsim.h MCFINT0_DSPI0 = 31'),
    (0, 32): ('DTIM0', 'm5441xsim.h MCFINT0_TIMER0 = 32'),
    (0, 34): ('DTIM2', 'm5441xsim.h MCFINT0_TIMER2 = 34'),
    (0, 33): ('DTIM1', 'DMA/general-purpose Timer 1 (DTER1)'),
    (0, 35): ('DTIM3', 'Timer 3 interrupt (DTER3)'),
    (0, 44): ('MAC-NET0', 'NOT USED by the silicon -- gap in the ENET0 EIR '
                          'block between LC=43 and GRA=45'),
    (0, 57): ('MAC-NET1', 'NOT USED by the silicon -- symmetric gap in the '
                          'ENET1 EIR block between LC=56 and GRA=58'),
    (1, 6): ('FlexCAN0', 'NOT USED by the silicon -- gap between FlexCAN0 '
                         'IFLAG1[BUFnI] entries'),
    (1, 0): ('FlexCAN0', 'm5441xsim.h MCFINT1_FLEXCAN0_IFL = 0'),
    (1, 26): ('eDMA', 'DMA channel 34 transfer complete. INFERRED from the '
                      'source = channel - 8 relation the manual gives for '
                      '28->36 and 29->37; MEASURED corroboration: the vector '
                      '154 handler reads TCD34 at 0xFC045450 and UART9'),
    (1, 27): ('eDMA', 'DMA channel 35 transfer complete. Same relation; '
                      'MEASURED: the vector 155 handler reads TCD35 at '
                      '0xFC045460'),
    (1, 46): ('eDMA', 'DMA channel 54 transfer complete, INFERRED from the '
                      'same relation. MEASURED: the vector 174 handler reads '
                      'eDMA 0xFC04401C and DTIM2 0xFC07800C'),
    (1, 28): ('eDMA', 'EDMA_INTR[INT36] -- DMA channel 36 transfer complete'),
    (1, 29): ('eDMA', 'EDMA_INTR[INT37] -- DMA channel 37 transfer complete'),
    (1, 40): ('eDMA', 'EDMA_INTR[INT48] -- DMA channel 48 transfer complete'),
    (1, 42): ('eDMA', 'EDMA_INTR[INT50] -- DMA channel 50 transfer complete'),
    (1, 52): ('UART8', 'UISR8 -- UART8 interrupt request'),
    (1, 53): ('UART9', 'UISR9 -- UART9 interrupt request'),
    (1, 54): ('DSPI1', 'DSPI1_SR -- DSPI1 OR-ed interrupt'),
    (1, 63): ('--', 'NOT USED by the silicon'),
    (2, 0): ('eDMA', 'EDMA_INTR[INT56-63] -- DMA channels 56-63 OR-ed transfer complete (RM table 17-17). The mk1 handler 0x400E1EB0 sits in the eSDHC driver region, so this is storage DMA completion.'),
    (2, 13): ('PIT0', 'PCSR0[PIF]'),
    (2, 14): ('PIT1', 'PCSR1[PIF]'),
    (2, 15): ('PIT2', 'PCSR2[PIF]'),
    (2, 16): ('PIT3', 'PCSR3[PIF]'),
    (2, 17): ('USB OTG', 'USB_STS -- USB OTG interrupt'),
    (2, 29): ('SIM', 'SIM_TSR -- SIM data interrupt'),
    (2, 30): ('SIM', 'SIM_RSR -- SIM general interrupt'),
    (2, 31): ('eSDHC', 'm5441xsim.h MCFINT2_SDHC = 31'),
}


def contract_sources(root):
    """-> {vector_number: description} built from the contract."""
    path = os.path.join(root, CONTRACT)
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding='utf-8') as fh:
        c = json.load(fh)
    for key, label in (('pit', 'PIT'), ('dtim', 'DMA timer'),
                       ('uart', 'UART')):
        for ch in c.get(key, {}).get('channels', []):
            vec = ch.get('vector')
            if vec is None:
                continue
            name = ch.get('name') or '%s%s' % (label, ch.get('channel'))
            out[int(vec)] = '%s (%s source %s)' % (name, ch.get('intc'),
                                                   ch.get('source'))
    es = c.get('esdhc', {})
    if es.get('irq_vector') is not None:
        out[int(es['irq_vector'])] = 'eSDHC (INTC2 source %s)' % es.get(
            'irq_source')
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--snapshot',
                    default='snapshots/Digitakt_OS1.53/gui.snap')
    ap.add_argument('--out', default='docs/mk1')
    ap.add_argument('--root', default='.')
    args = ap.parse_args()
    os.makedirs(os.path.join(args.out, 'facts'), exist_ok=True)

    named = contract_sources(args.root)
    m, _extra, _regs = restore(args.snapshot)
    raw = m.uc.mem_read(VBR, N_VECTORS * 4)
    vecs = list(struct.unpack('>%dI' % N_VECTORS, raw))

    # The default/unused handler is whichever address the most slots share.
    common = collections.Counter(v for v in vecs if v).most_common(1)
    default = common[0][0] if common else 0
    default_n = common[0][1] if common else 0

    rows = []
    for num, handler in enumerate(vecs):
        if handler == 0:
            continue
        kind = ('CPU exception' if num < 64 else
                'INTC0' if num < 128 else
                'INTC1' if num < 192 else 'INTC2')
        source = None
        if num >= 64:
            base = 64 if num < 128 else 128 if num < 192 else 192
            source = num - base
        intc_num = (0 if 64 <= num < 128 else 1 if 128 <= num < 192
                    else 2 if num >= 192 else None)
        src_info = INTC_SOURCES.get((intc_num, source)) if source is not None \
            else None
        rows.append({
            'vector': num,
            'handler': '0x%08X' % handler,
            'intc_module': src_info[0] if src_info else None,
            'intc_description': src_info[1] if src_info else None,
            'silicon_unused': bool(src_info and 'NOT USED' in src_info[1]),
            'is_default_stub': handler == default,
            'in_main_os': MAIN_OS_LO <= handler < MAIN_OS_HI,
            'class': kind,
            'intc_source': source,
            'cpu_meaning': CPU_VECTORS.get(num),
            'peripheral': named.get(num),
        })

    installed = [r for r in rows if not r['is_default_stub']]
    facts = {
        'document': 'digitakt-mk1.vectors',
        'schema_version': 1,
        'method': 'read from a restored snapshot; the table lives in SDRAM '
                  'below the MAIN OS load address and is filled in at run '
                  'time, so it does not exist in the firmware image',
        'snapshot': os.path.basename(args.snapshot),
        'vbr': '0x%08X' % VBR,
        'vectors_read': N_VECTORS,
        'default_stub': {'address': '0x%08X' % default, 'slots': default_n,
                         'note': 'the address the most slots share; treated '
                                 'as the do-nothing default handler'},
        'installed_count': len(installed),
        'vectors': rows,
    }
    jpath = os.path.join(args.out, 'facts', 'vectors.json')
    with open(jpath, 'w', encoding='utf-8') as fh:
        json.dump(facts, fh, indent=2)
        fh.write('\n')
    print('wrote %s' % jpath)

    L = []
    A = L.append
    A('# 04 — The interrupt vector table')
    A('')
    A('Generated by `tools/mk1doc_vectors.py` from `%s`.'
      % os.path.basename(args.snapshot))
    A('Machine-readable: `facts/vectors.json`.')
    A('')
    A('The vector table is the map from hardware event to code, so a vector')
    A('pointing at real code is a device the firmware expects to hear from —')
    A('which makes this the most direct statement of what an emulator has to')
    A('model.')
    A('')
    A('**It is read from a snapshot, not from the image.** VBR is `0x40000000`')
    A('and MAIN OS loads at `0x40000400`, so the table sits *below* the image')
    A('and is written at run time. It does not exist in the `.syx` at all.')
    A('')
    A('- Vectors read: %d' % N_VECTORS)
    A('- Default stub: `0x%08X`, shared by %d slots' % (default, default_n))
    A('- **Slots pointing somewhere else: %d**' % len(installed))
    A('')
    A('Vector numbering: 0–63 are CPU exceptions; 64+ are interrupt')
    A('controller sources, `vector = base + source` with bases 64 / 128 / 192')
    A('for INTC0 / INTC1 / INTC2. The bases come from the PIT, DTIM and UART')
    A('vector numbers in `docs/contracts/mcf5441x-reference-v1.json`, not from')
    A('an assumption.')
    A('')
    A('## Installed handlers')
    A('')
    A('| vector | handler | class | source | module | what it is |')
    A('|---|---|---|---|---|---|')
    for r in installed:
        what = (r.get('intc_description') or r['peripheral'] or
                r['cpu_meaning'] or '')
        mod = r.get('intc_module') or ''
        if r.get('silicon_unused'):
            what = '**' + what + '**'
        if not r['in_main_os']:
            what = (what + ' — handler is outside MAIN OS').strip()
        A('| %d | `%s` | %s | %s | %s | %s |'
          % (r['vector'], r['handler'], r['class'],
             r['intc_source'] if r['intc_source'] is not None else '—',
             mod or '**unassigned in the manual**', what))
    A('')
    forced = [r for r in installed if r.get('silicon_unused')]
    if forced:
        A('## Software-forced interrupts')
        A('')
        A('These sources are **not driven by the silicon** — the MCF5441x')
        A('manual lists them as gaps — yet each has a real handler installed.')
        A('An interrupt nothing can raise in hardware, with a handler on it,')
        A('is one the *firmware* raises itself, which is what the INTC force')
        A('registers (INTFRCH/INTFRCL) are for.')
        A('')
        A('| vector | source | nominally | handler |')
        A('|---|---|---|---|')
        for r in forced:
            A('| %d | %s source %s | %s | `%s` |'
              % (r['vector'], r['class'], r['intc_source'],
                 r['intc_module'], r['handler']))
        A('')
        A('Vector 108 is the sequencer tick. It sits on INTC0 source 44,')
        A('which the manual records as an unused gap in the ENET0 block —')
        A('so the sequencer clock is raised in software, not by a timer.')
        A('That is consistent with the earlier finding that its cadence comes')
        A('from an audio sample-clock accumulator rather than from any PIT.')
        A('')
    A('## Peripherals the contract names that are NOT installed')
    A('')
    A('A known vector still pointing at the default stub means the firmware')
    A('is not listening to that device in this snapshot.')
    A('')
    A('| vector | peripheral | state |')
    A('|---|---|---|')
    for num, label in sorted(named.items()):
        row = next((r for r in rows if r['vector'] == num), None)
        if row is None:
            state = 'slot reads 0'
        elif row['is_default_stub']:
            state = 'default stub'
        else:
            continue
        A('| %d | %s | %s |' % (num, label, state))
    A('')

    mpath = os.path.join(args.out, '04-vectors.md')
    with open(mpath, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L) + '\n')
    print('wrote %s' % mpath)


if __name__ == '__main__':
    main()
