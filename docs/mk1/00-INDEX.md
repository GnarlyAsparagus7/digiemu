# Digitakt mk1 (OS 1.53) — firmware documentation

**Read this file first.** It says what is known, what is not, and which
document answers which question.

The premise of this directory: the difficulty in emulating this device has
been *guessing*, not feasibility. So every fact here is read or computed from
the firmware image by a tool that can be re-run, and every fact carries a
confidence label. Where something is unknown it is listed as unknown rather
than filled in with a plausible answer.

## How to use this

Each document has a machine-readable twin under `facts/`. Prefer the JSON when
you are an agent; the Markdown is the same content for a person.

Documents 01–09 are **generated** — do not hand-edit them; the later ones
are written by hand. Regenerate with:

```sh
uv run python tools/mk1doc_container.py --syx Digitakt_OS1.53.syx --out docs/mk1
uv run python tools/mk1doc_mmio.py --out docs/mk1
uv run python tools/mk1doc_symbols.py --out docs/mk1
uv run python tools/mk1doc_vectors.py --out docs/mk1
uv run python tools/mk1doc_strings.py --out docs/mk1
uv run python tools/mk1doc_memory.py --syx Digitakt_OS1.53.syx --out docs/mk1
uv run python tools/mk1doc_tasks.py --syx Digitakt_OS1.53.syx --out docs/mk1
uv run python tools/mk1doc_coproc.py --syx Digitakt_OS1.53.syx --out docs/mk1
uv run python tools/mk1doc_unknowns.py --out docs/mk1   # run last
```

## Confidence labels

Used consistently in every table:

| label | meaning |
|---|---|
| **measured** | read directly from the firmware bytes |
| **derived** | computed from measured values by a stated rule |
| **inferred** | a judgement from evidence; the evidence is given so it can be challenged |
| **UNKNOWN** | not established. Not a gap in the writing — a gap in the knowledge |

## Documents

| # | file | covers | status |
|---|---|---|---|
| 01 | `01-container.md` | SysEx transport, ELE3 header field by field, all 5 sections with hashes and architecture | **done** |
| 03 | `03-peripherals.md` | every hardware block the MAIN OS references, named, with what models it | **done** |
| 02 | `02-memory-map.md` | nominal SoC map vs the regions actually mapped, and where each section loads | **done** |
| 04 | `04-vectors.md` | the interrupt vector table and every handler the firmware installs | **done** |
| 05 | `05-rtos.md` | TCB layout and the task inventory, with its capture limits stated | **done** (partial inventory) |
| 06 | `06-symbols.md` | full symbol inventory: resolved with evidence, and the 1 still unresolved | **done** |
| 07 | `07-strings.md` | string-pointer tables found structurally: where they are and their shape, not their contents | **done** |
| 08 | `08-coprocessor.md` | section id 8: padding, decode coverage, entropy, strings | **done** (load address still unknown) |
| 09 | `09-unknowns.md` | the consolidated open list, generated from the other fact files | **done** |
| 10 | `10-plusdrive.md` | the +Drive card map, measured by driving the firmware's own FORMAT +DRIVE | **done** (region contents still open) |
| 11 | `11-audio.md` | where audio really runs, and how far the emulator drives it | **done** (the chain runs; it renders silence) |

## What the finished documents establish

### Container (01)

Five sections. MAIN OS is section id 3, 2,475,584 bytes, loading at
`0x40000400`. The ELE3 header is decoded field by field; two of its fields
(`+0x04` and `+0x18`) have **measured values but unknown meaning**, and are
recorded as such. Two sections do not decompress with `dt2/elz.py` — the
updater (id 4) and a 15-byte metadata section (id 5) — which is a known
limitation of that depacker, not a property of this firmware. Neither is
needed to run the emulator.

### Peripherals (03)

The inventory is **opcode-anchored**: an address counts as referenced only
when it sits in the operand position of an m68k absolute-long instruction.
This matters. An earlier naive scan — every 32-bit value in a 64 MB window —
produced a table dominated by float bit patterns and negative numbers, and it
answered the coprocessor question backwards.

Headline results:

| block | refs | modelled by |
|---|---|---|
| DSPI0 `0xFC05C000` | 313 | **nothing** |
| eDMA `0xFC044000` | 213 | `emu/edma.py` |
| GPIO `0xEC094000` | 192 | **nothing** |
| USB OTG `0xFC0B0000` | 143 | **nothing** |
| eSDHC `0xFC0CC000` | 117 | `emu/esdhc.py` |
| DSPI1 `0xFC03C000` | 47 | **nothing** |
| INTC0/1/2 | 69 combined | **nothing** |
| UART9 (MIDI DIN) `0xEC074000` | 20 | **nothing** |

### Symbols (06)

81 symbols are looked for in this image: **80 resolved, 1 not**. The rule
that resolved each one is recorded, because the rule *is* the confidence:

| confidence | count | what it means |
|---|---|---|
| mixed (AnyOf) | 28 | first sub-rule that resolved won |
| derived | 28 | computed from another resolved symbol |
| measured | 20 | a masked signature matching exactly once in the image |
| unclassified | 4 | rule type not yet categorised by the tool |

The bare-literal category is gone. It held 17 asserted addresses, and every
one is now an `AnyOf(Sig, Fixed)` whose signature is tried first, so a wrong
image fails on the signature rather than on a verify byte.

**The one unresolved symbol is `px_copy`, and it is absent rather than
unfound.** `px_copy_to_bitmap(PixelData&, Bitmap&)` is a Digitakt II routine;
mk1 carries no `PixelData` string and no match for its two-pointer prologue,
and renders through `panel_diff` and the `fb_front`/`fb_back` pair instead.
It costs one diagnostic counter.

### Interrupt vectors (04)

Read from a snapshot, not the image: VBR is `0x40000000` and MAIN OS loads at
`0x40000400`, so the table sits *below* the image and is written at run time.

**28 slots point somewhere other than the default stub.** Named ones
corroborate addresses used elsewhere in this project -- vector 208 is the
display PIT3 handler `0x400E5CEC`, vector 99 is DTIM3 `0x4005F150`, vector 223
is eSDHC `0x400E1DCA`, vectors 180/181 are UART8/UART9. Vector 205 (PIT0)
points at `0x40000410`, which is `ctx_switch` -- the scheduler runs off PIT0.

**17 installed vectors have no peripheral name**, including INTC0 source 44
(vector 108 -> `0x4006E756`), which is the sequencer tick. Those are listed in
09.

### Unknowns (09)

**12 open items: 11 emulation gaps, 1 symbol. Nothing is left
unidentified.** The categories that held genuine unknowns are empty:

| category | at the start | now |
|---|---|---|
| container | 4 | **0** |
| hardware | 3 | **0** |
| interrupts | 17 | **0** |
| symbols | 9 | **1**, and that one is absent from the image |
| emulation gap | 9 | 11 |

The count started at 42. It did not fall by deciding things were unimportant;
each item was closed against a primary source or a measurement, and the
"emulation gap" number ROSE at first because naming a block moves it out of
"unidentified" and into "known, and nothing models it". It has started
falling again as those blocks get models: SSI1 left the list when
`emu/ssi.py` and `emu/edma_sw.py` landed.

What the two remaining categories actually are:

- **11 emulation gaps** — named peripherals with no model: DSPI0, DSPI1,
  GPIO, USB OTG, INTC0/1/2, UART9, EPORT0, SCM, CCM. This is a build queue,
  not missing knowledge. Every one is identified, reference-counted and
  cross-checked against the vector table.
- **1 optional symbol** — `px_copy`, which costs one diagnostic counter and
  does not exist in this image. **Nothing unresolved affects emulation
  correctness.**

### Peripheral modules and the clock list (03)

The slot number in the MCF5441x memory map **is** the PPMCR module number, so
the firmware's own clock-enable writes name the hardware it powers up. All
eleven decode, and every one is independently corroborated by a reference count
or an installed vector:

DSPI1 (15), eDMA (17), INTC0/1/2 (18/19/20), **DSPI0 (23)**, DMA timer 1 (29),
DMA timer 3 (31), PIT0 (32), Edge port 0 (36), USB OTG (44).

DSPI0 is both the most-referenced block in the image (313) and clock-enabled,
and nothing models it.

### An audio finding that contradicts an earlier note

`0xFC0C8000` is **SSI 1**, a synchronous serial interface — audio. MAIN OS
writes it 21 times across 7 register offsets from one init function at
`0x40000F80`–`0x40001010`.

An earlier note in this project recorded "zero references to SSI0/SSI1
(0xFC0BC000/0xFC0C0000)" and concluded audio is not on the ColdFire at all.
`0xFC0C0000` is the **PLL**, not SSI1 — so that check looked at the wrong
address. SSI0 at `0xFC0BC000` is indeed unreferenced, but SSI1 is not.

That was written as a lead. **It is now a conclusion, and `11-audio.md` is
the document.** The engine is `FUN_40077420` on vector 191, rendering into a
512-byte double buffer at `0x4ba8f080` that eDMA channel 54 drains into SSI1,
computing with the EMAC accumulators. `emu/ssi.py` models the interface and
`emu/edma_sw.py` the software-started channels, and the render loop runs
continuously under emulation. It renders silence, which is correct for a
machine with an empty +Drive.

Still not established: module 50 never appears in MAIN OS's PPMCR writes, so
whether SSI1's clock is enabled elsewhere is unknown, and the input clock is
unknown so neither is the output sample rate.

## Corrections this documentation forced

**`0x8C000000` is not used by mk1.** Earlier in this project it was asserted
that it is, on the strength of 39 raw byte-pattern matches. Of those 39, **38
are at odd byte offsets**, and m68k operands are always 2-byte aligned, so
they cannot be instruction operands. Opcode-anchored references: **zero**.

Consequences, which reach into work already done:

- `dsp=True` on mk1 models a FlexBus port the firmware never touches. Every
  reading of the dsp Fifo counters (`polls`/`words`/`bursts`) taken on mk1 is
  vacuous rather than informative — including the `polls=0 words=0 bursts=0`
  used earlier to argue the coprocessor was idle. That argument does not hold;
  it was measuring a port that does not exist here.
- `emu/gui.py`'s note that without `dsp=True` "the priority-3 job worker
  wedges in the ready-bit spin at 0x400cf4ec" is a **Digitakt II** fact. It
  should not be carried over to mk1 without re-establishing it.
- The mk1 coprocessor link is DSPI. That is consistent with the evidence and
  is **not proven** by this document — what travels over DSPI0 has not been
  established.

## Open questions at the hardware layer

**There are no unidentified blocks left.** The three that were listed here
are named and two of them have models:

| base | what it is | modelled by |
|---|---|---|
| `0xFC0C8000` | SSI 1, audio | `emu/ssi.py` |
| `0xFC040000` | SCM, holds PPMSR/PPMCR/WCR | **nothing** |
| `0xFC090000` | EPORT0, edge port | **nothing** |

What is open is now a build queue rather than a naming problem, and
`09-unknowns.md` is the list.

`0x80000000`/`0x8000C000`/`0x80008000`/`0x80004000` also appear; those are
internal SRAM (64 KiB of data), not peripheral registers.

## Rule for anyone continuing

Do not add a fact here without saying how it was measured. If a tool cannot
produce it, it belongs in `09-unknowns.md` until one can.
