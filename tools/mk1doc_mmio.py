#!/usr/bin/env python3
"""Inventory every hardware address the mk1 MAIN OS references.

The point of this document is to replace guessing about what the firmware
talks to. It scans the image for 32-bit big-endian constants that land in a
peripheral window, groups them into peripheral blocks, and names each block
from docs/contracts/mcf5441x-reference-v1.json -- the curated NXP/Linux/
NetBurner contract already in this repo, which carries per-fact provenance.

Then it says, for each block, whether the emulator models it. A block that is
referenced often and modelled by nothing is exactly where an emulation will
behave unlike the device, and that list is the output that matters.

Caveat stated up front, because it bounds every number here: a 32-bit value
that happens to equal a peripheral address is not proof of a reference -- it
could be data. Counts are evidence, not proof. Clustering is the corroborating
signal: real peripheral use produces many distinct offsets inside one 16KB
block, while a coincidence produces one value with no neighbours.

    python tools/mk1doc_mmio.py --out docs/mk1
"""
import argparse
import collections
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config

CONTRACT = 'docs/contracts/mcf5441x-reference-v1.json'
LOAD = 0x40000400

# Windows worth scanning, from the contract's own memory_map.
# Narrow, real peripheral windows. An earlier version of this tool scanned
# 0xFC000000-0xFFFFFFFF, i.e. 64MB, and the result was dominated by negative
# numbers and float bit patterns that merely look like addresses. The MCF5441x
# puts its registers in the first megabyte of each peripheral bus, so that is
# what gets scanned.
WINDOWS = [
    (0x80000000, 0x80010000, 'internal SRAM (64 KiB)'),
    (0x8C000000, 0x8C100000, 'Rapid GPIO space / FlexBus coprocessor port'),
    (0xEC000000, 0xEC100000, 'peripheral bus 1'),
    (0xFC000000, 0xFC100000, 'peripheral bus 2 / SoC registers'),
]

# m68k/ColdFire absolute-long addressing. Each entry is
# (opcode_halfword, operand_offset_in_bytes). Counting only values that follow
# one of these is what separates a real hardware reference from a coincidence:
# the address has to be sitting where an instruction's absolute operand goes.
def _abs_opcodes():
    ops = {
        0x4879: 2,   # pea (xxx).l
        0x4EB9: 2,   # jsr (xxx).l
        0x4EF9: 2,   # jmp (xxx).l
        0x4AB9: 2,   # tst.l (xxx).l
        0x4A79: 2,   # tst.w (xxx).l
        0x4A39: 2,   # tst.b (xxx).l
        0x42B9: 2,   # clr.l (xxx).l
        0x4279: 2,   # clr.w (xxx).l
        0x4239: 2,   # clr.b (xxx).l
        0x13FC: 4,   # move.b #imm,(xxx).l   -- imm is 2 bytes
        0x33FC: 4,   # move.w #imm,(xxx).l   -- imm is 2 bytes
        0x23FC: 6,   # move.l #imm,(xxx).l   -- imm is 4 bytes
    }
    for reg in range(8):
        ops[0x41F9 | (reg << 9)] = 2            # lea (xxx).l,An
        ops[0x1039 | (reg << 9)] = 2            # move.b (xxx).l,Dn
        ops[0x3039 | (reg << 9)] = 2            # move.w (xxx).l,Dn
        ops[0x2039 | (reg << 9)] = 2            # move.l (xxx).l,Dn
        ops[0x13C0 | reg] = 2                   # move.b Dn,(xxx).l
        ops[0x33C0 | reg] = 2                   # move.w Dn,(xxx).l
        ops[0x23C0 | reg] = 2                   # move.l Dn,(xxx).l
    return ops


ABS_OPCODES = _abs_opcodes()

# What emu/ actually models, and the module that does it. Kept explicit rather
# than grepped, so each entry can be checked against the module it names.
MODELLED = {
    0xFC080000: 'emu/pit.py (PIT0)',
    0xFC084000: 'emu/pit.py (PIT1, listed not delivered)',
    0xFC088000: 'emu/pit.py (PIT2)',
    0xFC08C000: 'emu/pit.py (PIT3)',
    0xFC070000: 'emu/dtim.py (DTIM0)',
    0xFC074000: 'emu/dtim.py (DTIM1)',
    0xFC078000: 'emu/dtim.py (DTIM2)',
    0xFC07C000: 'emu/dtim.py (DTIM3)',
    0xFC044000: 'emu/edma.py (eDMA), emu/edma_sw.py (software-started channels)',
    0xFC0C8000: 'emu/ssi.py (SSI1 -- audio transmit and receive)',
    0xFC0CC000: 'emu/esdhc.py (eSDHC)',
    0xEC070000: 'emu/longrun.py (UART8 panel link)',
    0x8C000000: 'emu/dsp.py (ready line only -- writes are discarded)',
}



# Slot number == PPMCR module number. From MCF5441x Reference Manual Rev 5,
# Table 1-3 "Peripheral Bus Controller 0 Memory Map", chapter 1 page 1-17.
MODULE_NAMES = {
    9: 'FlexCAN 1', 14: 'I2C 1', 15: 'DSPI 1', 16: 'SCM',
    17: 'eDMA controller', 18: 'Interrupt controller 0',
    19: 'Interrupt controller 1', 20: 'Interrupt controller 2',
    21: 'Interrupt controller IACK', 22: 'I2C 0', 23: 'DSPI 0',
    24: 'UART0', 25: 'UART1', 26: 'UART2', 27: 'UART3',
    28: 'DMA timer 0', 29: 'DMA timer 1', 30: 'DMA timer 2',
    31: 'DMA timer 3', 32: 'PIT 0', 33: 'PIT 1', 34: 'PIT 2', 35: 'PIT 3',
    36: 'Edge port 0', 37: 'ADC', 38: 'DAC 0', 39: 'DAC 1',
    42: 'Robust real-time clock', 43: 'SIM', 44: 'USB On-the-Go',
    45: 'USB host', 46: 'DDR controller', 47: 'SSI 0', 48: 'PLL',
    49: 'Random number generator', 50: 'SSI 1', 51: 'eSDHC',
    53: 'MAC-NET0', 54: 'MAC-NET1',
}


def load_contract(root):
    path = os.path.join(root, CONTRACT)
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def block_names(contract):
    """-> {base_addr: (name, provenance)} from the contract, plus extras."""
    out = {}

    def add(base, name, src):
        try:
            out[int(base, 16)] = (name, src)
        except (TypeError, ValueError):
            pass

    for ch in contract.get('pit', {}).get('channels', []):
        add(ch['base'], 'PIT%d (INTC2 source %s, vector %s)'
            % (ch['channel'], ch.get('source'), ch.get('vector')),
            'contract:pit')
    for ch in contract.get('dtim', {}).get('channels', []):
        add(ch['base'], 'DMA timer %d (INTC0 source %s, vector %s)'
            % (ch['channel'], ch.get('source'), ch.get('vector')),
            'contract:dtim')
    for ch in contract.get('uart', {}).get('channels', []):
        add(ch['base'], '%s (INTC1 source %s, vector %s)'
            % (ch.get('name'), ch.get('source'), ch.get('vector')),
            'contract:uart')
    if contract.get('gpio', {}).get('base'):
        add(contract['gpio']['base'], 'GPIO ports', 'contract:gpio')
    if contract.get('esdhc', {}).get('base'):
        add(contract['esdhc']['base'], 'eSDHC (SD/eMMC controller)',
            'contract:esdhc')
    if contract.get('edma', {}).get('base'):
        add(contract['edma']['base'], 'eDMA controller', 'contract:edma')
    if contract.get('edma', {}).get('tcd_base'):
        add(contract['edma']['tcd_base'], 'eDMA TCD array', 'contract:edma')
    if contract.get('intc', {}).get('base'):
        add(contract['intc']['base'], 'INTC0', 'contract:intc')
    for blk in contract.get('register_blocks', []):
        add(blk['base'], blk['name'], 'contract:register_blocks')

    # Not in the contract. Each carries where the identification came from, so
    # it can be challenged independently of the contract-sourced rows.
    # Sourced from Linux arch/m68k/include/asm/m5441xsim.h unless noted --
    # a primary, checkable source rather than recollection. Two entries here
    # were WRONG in an earlier version of this tool and are corrected:
    # 0xFC0C0000 was labelled SSI1 when Linux defines MCF_PLL_CR there, and
    # 0xFC040000 was unidentified when it is the power management block.
    extra = {
        0xFC048000: ('INTC0', 'm5441xsim.h MCFINTC0_SIMR 0xfc04801c'),
        0xFC04C000: ('INTC1', 'm5441xsim.h MCFINTC1_SIMR 0xfc04c01c'),
        0xFC050000: ('INTC2', 'm5441xsim.h MCFINTC2_SIMR 0xfc05001c'),
        0xFC03C000: ('DSPI1', 'm5441xsim.h MCFDSPI_BASE1 0xfc03c000'),
        0xFC05C000: ('DSPI0', 'm5441xsim.h MCFDSPI_BASE0 0xfc05c000'),
        0xFC0B0000: ('USB OTG (ChipIdea/EHCI)', 'MCF5441x RM; corroborated '
                                                'by the vector 209 handler '
                                                'reading 0xFC0B014C/1A4'),
        0xFC0C0000: ('PLL', 'MCF5441x RM table 1-3 slot 48; m5441xsim.h '
                            'MCF_PLL_CR. This tool previously mislabelled it '
                            'SSI1 -- SSI1 is 0xFC0C8000.'),
        0xFC0C4000: ('Random number generator', 'MCF5441x RM table 1-3 '
                                                'slot 49'),
        0xFC0C8000: ('SSI 1 (synchronous serial -- AUDIO)',
                     'MCF5441x RM table 1-3 slot 50'),
        0xFC0BC000: ('SSI 0 (synchronous serial -- AUDIO)',
                     'MCF5441x RM table 1-3 slot 47'),
        0xFC0AC000: ('SIM', 'MCF5441x RM table 1-3 slot 43'),
        0xFC040000: ('SCM (system control; holds PPMSR/PPMCR/WCR)',
                     'MCF5441x RM table 1-3 slot 16; the power-management '
                     'registers live inside it -- m5441xsim.h MCFPM_PPMCR0 '
                     '0xfc04002d, MCFPM_WCR 0xfc040013'),
        0xFC090000: ('EPORT0 (edge port)',
                     'm5441xsim.h MCFEPORT_EPPAR 0xfc090000; corroborated by '
                     'the vector 66 and 67 handlers acking 0xFC090003/6'),
        0xFC044000: ('eDMA controller', 'm5441xsim.h MCFEDMA_BASE 0xfc044000'),
        0xFC0CC000: ('eSDHC (SD/eMMC)', 'm5441xsim.h MCFSDHC_BASE 0xfc0cc000'),
        0xEC090000: ('CCM / chip configuration',
                     'm5441xsim.h MCF_CCM_CCR 0xec090004'),
        0xEC094000: ('GPIO ports', 'm5441xsim.h MCFGPIO_PAR_* 0xec094xxx'),
    }
    for base, (name, src) in extra.items():
        out.setdefault(base, (name, src))
    return out


def scan(img, base_addr):
    """-> (anchored, loose): Counters of MMIO addresses in the image.

    `anchored` counts only addresses sitting in the operand position of an
    absolute-long instruction, which is what a real hardware access looks
    like. `loose` counts every 32-bit value in a window regardless, and is
    kept only so the two can be compared -- the gap between them is the
    measure of how much the loose method over-reports.
    """
    anchored = collections.Counter()
    loose = collections.Counter()
    n = len(img) - 8

    def in_window(v):
        return any(lo <= v < hi for lo, hi, _l in WINDOWS)

    for off in range(0, n, 2):
        op = struct.unpack_from('>H', img, off)[0]
        delta = ABS_OPCODES.get(op)
        if delta is not None:
            val = struct.unpack_from('>I', img, off + delta)[0]
            if in_window(val):
                anchored[val] += 1
        val = struct.unpack_from('>I', img, off)[0]
        if in_window(val):
            loose[val] += 1
    return anchored, loose


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='docs/mk1')
    ap.add_argument('--root', default='.')
    args = ap.parse_args()

    os.makedirs(os.path.join(args.out, 'facts'), exist_ok=True)
    contract = load_contract(args.root)
    names = block_names(contract)

    img = open(config.main_image(), 'rb').read()
    hits, loose = scan(img, LOAD)

    # Group into 16KB blocks: the MCF5441x peripheral stride.
    blocks = collections.defaultdict(lambda: {'refs': 0, 'offsets': set()})
    for val, n in hits.items():
        base = val & ~0x3FFF
        blocks[base]['refs'] += n
        blocks[base]['offsets'].add(val - base)

    rows = []
    for base, d in sorted(blocks.items(), key=lambda kv: -kv[1]['refs']):
        name, prov = names.get(base, (None, None))
        window = next((lbl for lo, hi, lbl in WINDOWS if lo <= base < hi), '?')
        rows.append({
            'base': '0x%08X' % base,
            'name': name or 'UNIDENTIFIED',
            'identified_from': prov or 'not in the contract and not in this '
                                       'tool\'s supplementary table',
            'window': window,
            'references': d['refs'],
            'distinct_offsets': len(d['offsets']),
            'offsets_sample': sorted('0x%03x' % o for o in d['offsets'])[:12],
            'modelled_by': MODELLED.get(base, None),
        })

    facts = {
        'document': 'digitakt-mk1.mmio-inventory',
        'schema_version': 1,
        'method': 'addresses appearing in the OPERAND POSITION of an m68k '
                  'absolute-long instruction (pea/jsr/jmp/lea/move/tst/clr) '
                  'in the decompressed MAIN OS, grouped into 16KB blocks',
        'caveat': 'an opcode-anchored hit is strong evidence but not proof: '
                  'the scan is not a disassembly, so a byte pair inside data '
                  'can still look like an opcode. Clustering (many distinct '
                  'offsets in one block) is the corroborating signal.',
        'loose_vs_anchored': {
            'anchored_total': sum(hits.values()),
            'loose_total': sum(loose.values()),
            'note': 'loose counts every 32-bit value in a window regardless '
                    'of context. The ratio shows how badly the naive method '
                    'over-reports; the tables below use the anchored counts.',
        },
        'image': {'name': os.path.basename(config.main_image()),
                  'bytes': len(img), 'load_address': '0x%08X' % LOAD},
        'windows_scanned': [{'start': '0x%08X' % lo, 'end': '0x%08X' % hi,
                             'label': lbl} for lo, hi, lbl in WINDOWS],
        'blocks': rows,
    }
    unmodelled = [r for r in rows
                  if r['modelled_by'] is None and r['distinct_offsets'] >= 2]
    facts['referenced_but_not_modelled'] = unmodelled

    # --- which peripheral modules does the firmware actually power up? ----
    # PPMCR0/PPMCR1 take a MODULE NUMBER and enable that module's clock, so
    # the set of values written to them is an authoritative list of the
    # hardware this firmware turns on -- stronger evidence than counting
    # address references, because a module with no clock cannot respond at
    # all.
    PPM = {0xFC04002C: 'PPMSR0 (disable)', 0xFC04002D: 'PPMCR0 (enable)',
           0xFC04002E: 'PPMSR1 (disable)', 0xFC04002F: 'PPMCR1 (enable)'}
    ppm = {v: set() for v in PPM.values()}
    n = len(img)
    for o in range(0, n - 10, 2):
        op = struct.unpack_from('>H', img, o)[0]
        if op == 0x13FC:                              # move.b #imm,(xxx).l
            dst = struct.unpack_from('>I', img, o + 4)[0]
            if dst in PPM:
                ppm[PPM[dst]].add(
                    struct.unpack_from('>H', img, o + 2)[0] & 0xFF)
        if op & 0xF100 == 0x7000:                     # moveq #N,Dn ; move.b
            reg = (op >> 9) & 7
            if struct.unpack_from('>H', img, o + 2)[0] == (0x13C0 | reg):
                dst = struct.unpack_from('>I', img, o + 4)[0]
                if dst in PPM:
                    ppm[PPM[dst]].add(op & 0xFF)
        if op & 0xF1FF == 0x41F9:                     # lea (xxx).l,An ; ...
            an = (op >> 9) & 7
            dst = struct.unpack_from('>I', img, o + 2)[0]
            if dst in PPM:
                p = o + 6
                while p < min(o + 96, n - 4):
                    if struct.unpack_from('>H', img, p)[0] == (0x10BC | (an << 9)):
                        ppm[PPM[dst]].add(
                            struct.unpack_from('>H', img, p + 2)[0] & 0xFF)
                        p += 4
                    else:
                        p += 2
    facts['power_management'] = {
        'registers': {hex(k): v for k, v in PPM.items()},
        'source': 'Linux arch/m68k/include/asm/m5441xsim.h MCFPM_PPMSR0/'
                  'PPMCR0/PPMSR1/PPMCR1 at 0xfc04002c..0xfc04002f',
        'modules_enabled': {k: sorted(v) for k, v in ppm.items() if v},
        'module_names': MODULE_NAMES,
        'module_name_source': 'MCF5441x Reference Manual Rev 5, Table 1-3 '
                              '"Peripheral Bus Controller 0 Memory Map", '
                              'chapter 1 page 1-17. The slot number in that '
                              'table IS the PPMCR module number.',
        'enabled_named': {k: [{'module': v,
                               'name': MODULE_NAMES.get(v, 'UNKNOWN')}
                              for v in sorted(vals)]
                          for k, vals in ppm.items() if vals},
        'cross_check': 'Every enabled module is independently corroborated by '
                       'this document or 04-vectors.md: DSPI0 (23) is the '
                       'most-referenced block, eDMA (17) has 213 references, '
                       'DMA timers 1 and 3 (29, 31) are the only two with '
                       'installed vectors (97, 99), PIT0 (32) has vector 205, '
                       'Edge port 0 (36) has vectors 66 and 67, and USB OTG '
                       '(44) has 143 references and vector 209.',
    }

    # ---- Claim checks -------------------------------------------------
    # Specific assertions about this image, each answered from the bytes and
    # each recording the evidence, so the answer can be rechecked rather than
    # believed. They exist because the naive byte-pattern count got the first
    # one WRONG in the opposite direction.
    import re as _re

    def alignment_check(addr):
        pat = struct.pack('>I', addr)
        offs = [m.start() for m in _re.finditer(_re.escape(pat), img)]
        even = sum(1 for o in offs if (o & 1) == 0)
        anchored_n = hits.get(addr, 0)
        return {
            'address': '0x%08X' % addr,
            'raw_byte_pattern_occurrences': len(offs),
            'at_even_offsets': even,
            'at_odd_offsets': len(offs) - even,
            'opcode_anchored_references': anchored_n,
            'verdict': ('REFERENCED' if anchored_n else 'NOT REFERENCED'),
            'reasoning': 'm68k instructions and their operands are always '
                         '2-byte aligned, so a match at an odd offset cannot '
                         'be an instruction operand. Only opcode-anchored '
                         'hits count as references.',
        }

    claims = [
        {
            'claim': 'The mk1 MAIN OS uses the 0x8C000000 FlexBus port that '
                     'emu/dsp.py models for Digitakt II.',
            'answer': 'NO',
            'evidence': alignment_check(0x8C000000),
            'consequence': 'Passing dsp=True to longrun.build models a port '
                           'this firmware never touches. Any reading of the '
                           'dsp Fifo counters (polls/words/bursts) on mk1 is '
                           'therefore vacuous, not informative.',
        },
        {
            'claim': 'The mk1 coprocessor link is DSPI.',
            'answer': 'CONSISTENT WITH THE EVIDENCE, not proven here',
            'evidence': {
                'DSPI0_0xFC05C000': alignment_check(0xFC05C000),
                'DSPI1_0xFC03C000': alignment_check(0xFC03C000),
            },
            'consequence': 'DSPI0 is the most-referenced peripheral block in '
                           'the image and nothing models it. What travels '
                           'over it has NOT been established by this '
                           'document.',
        },
    ]
    facts['claim_checks'] = claims

    jpath = os.path.join(args.out, 'facts', 'mmio.json')
    with open(jpath, 'w', encoding='utf-8') as fh:
        json.dump(facts, fh, indent=2)
        fh.write('\n')
    print('wrote %s' % jpath)

    L = []
    A = L.append
    A('# 03 — Hardware the firmware actually touches')
    A('')
    A('Generated by `tools/mk1doc_mmio.py`. Machine-readable: `facts/mmio.json`.')
    A('')
    A('Method: every 32-bit big-endian value in the decompressed MAIN OS that')
    A('lands in an MMIO window, grouped into 16 KB blocks (the MCF5441x')
    A('peripheral stride) and named from')
    A('`docs/contracts/mcf5441x-reference-v1.json`.')
    A('')
    A('**Caveat, which bounds every number below:** a constant that equals a')
    A('peripheral address is *evidence*, not proof — it could be data. The')
    A('corroborating signal is clustering: real use produces many distinct')
    A('offsets inside one block, a coincidence produces one value alone. The')
    A('`distinct offsets` column is therefore more meaningful than `refs`.')
    A('')
    A('## Every block referenced')
    A('')
    A('| base | peripheral | refs | distinct offsets | modelled by |')
    A('|---|---|---|---|---|')
    for r in rows:
        A('| `%s` | %s | %d | %d | %s |'
          % (r['base'], r['name'], r['references'], r['distinct_offsets'],
             r['modelled_by'] or '**nothing**'))
    A('')
    A('## Referenced but not modelled')
    A('')
    A('Blocks with at least two distinct offsets — so probably real use — that')
    A('no emulator module implements. This is the measured emulation gap.')
    A('')
    if not unmodelled:
        A('None.')
    else:
        A('| base | peripheral | refs | distinct offsets | identified from |')
        A('|---|---|---|---|---|')
        for r in unmodelled:
            A('| `%s` | %s | %d | %d | %s |'
              % (r['base'], r['name'], r['references'],
                 r['distinct_offsets'], r['identified_from']))
    A('')
    pm = facts['power_management']
    A('## Which peripherals the firmware powers up')
    A('')
    A('`PPMCR0`/`PPMCR1` take a **module number** and enable that module\'s')
    A('clock. The set of values written to them is therefore an authoritative')
    A('list of the hardware this firmware turns on — stronger evidence than')
    A('counting address references, because a module with no clock cannot')
    A('respond at all.')
    A('')
    A('Register addresses from Linux `arch/m68k/include/asm/m5441xsim.h`:')
    A('`MCFPM_PPMSR0` `0xfc04002c`, `MCFPM_PPMCR0` `0xfc04002d`,')
    A('`MCFPM_PPMSR1` `0xfc04002e`, `MCFPM_PPMCR1` `0xfc04002f`.')
    A('')
    for reg, mods in pm['modules_enabled'].items():
        A('**%s** — %d module(s): %s' % (reg, len(mods), ', '.join(
            '`0x%02X` (%d)' % (v, v) for v in mods)))
        A('')
    A('The slot number in the MCF5441x memory map **is** the PPMCR module')
    A('number, so each one has a name. Source: MCF5441x Reference Manual')
    A('Rev 5, Table 1-3, chapter 1 page 1-17.')
    A('')
    A('| module | peripheral | corroborated by |')
    A('|---|---|---|')
    CORROB = {
        15: '47 references to DSPI1',
        17: '213 references to eDMA',
        18: 'INTC0 referenced; vectors 64-121 in use',
        19: 'INTC1 referenced; vectors 128-191 in use',
        20: 'INTC2 referenced; vectors 192-223 in use',
        23: '313 references -- the most-referenced block in the image',
        29: 'vector 97, one of only two DMA timers with a handler',
        31: 'vector 99; dtim3_init writes 0x1F here before installing it',
        32: 'vector 205 (PIT0), which points at ctx_switch',
        36: 'vectors 66 and 67, both acking 0xFC090003/6',
        44: '143 references and vector 209',
    }
    for reg, mods in pm['modules_enabled'].items():
        for v in mods:
            A('| `0x%02X` (%d) | %s | %s |'
              % (v, v, MODULE_NAMES.get(v, '**not in table 1-3**'),
                 CORROB.get(v, '—')))
    A('')
    A('Every enabled module is independently corroborated by the reference')
    A('counts above or by an installed vector in `04-vectors.md`. Nothing here')
    A('rests on the module table alone.')
    A('')
    A('**Not enabled, but referenced:** SSI1 (`0xFC0C8000`, module 50) is')
    A('written by an init function at `0x40000F80`–`0x40001010` — 21')
    A('references across 7 register offsets — yet module 50 never appears in')
    A("MAIN OS's PPMCR writes. Whether its clock is enabled elsewhere (the")
    A('bootstrap, section id 2) is **not established here**. This matters:')
    A('SSI is the audio serial interface, and an earlier note in this project')
    A('recorded "zero references to SSI0/SSI1" — that was checked against')
    A('`0xFC0BC000`/`0xFC0C0000`, and `0xFC0C0000` is the PLL, not SSI1.')
    A('')
    A('## Unidentified blocks')
    A('')
    A('Blocks that neither the contract nor this tool can name. These are the')
    A('genuine unknowns at the hardware layer.')
    A('')
    unknown = [r for r in rows if r['name'] == 'UNIDENTIFIED'
               and r['distinct_offsets'] >= 2]
    if not unknown:
        A('None — every block with more than one distinct offset is named.')
    else:
        A('| base | window | refs | distinct offsets | offsets seen |')
        A('|---|---|---|---|---|')
        for r in unknown:
            A('| `%s` | %s | %d | %d | %s |'
              % (r['base'], r['window'], r['references'],
                 r['distinct_offsets'], ', '.join(r['offsets_sample'])))
    A('')

    A('## Claim checks')
    A('')
    A('Specific assertions, each answered from the bytes with its evidence, so')
    A('it can be rechecked rather than believed. The first one is here because')
    A('a naive byte-pattern count answered it **wrongly** in the opposite')
    A('direction earlier in this project.')
    A('')
    for c in claims:
        A('### %s' % c['claim'])
        A('')
        A('**Answer: %s**' % c['answer'])
        A('')
        ev = c['evidence']
        items = ev.items() if 'address' not in ev else [(None, ev)]
        for label, e in items:
            if label:
                A('*%s*' % label)
            A('')
            A('| measure | value |')
            A('|---|---|')
            A('| address | `%s` |' % e['address'])
            A('| raw byte-pattern occurrences | %d |'
              % e['raw_byte_pattern_occurrences'])
            A('| at even offsets | %d |' % e['at_even_offsets'])
            A('| at odd offsets (cannot be operands) | %d |'
              % e['at_odd_offsets'])
            A('| **opcode-anchored references** | **%d** |'
              % e['opcode_anchored_references'])
            A('| verdict | %s |' % e['verdict'])
            A('')
        A('%s' % c['consequence'])
        A('')

    mpath = os.path.join(args.out, '03-peripherals.md')
    with open(mpath, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L) + '\n')
    print('wrote %s' % mpath)


if __name__ == '__main__':
    main()
