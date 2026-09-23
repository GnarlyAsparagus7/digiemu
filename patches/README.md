# Patches

Five diffs against [Unicorn Engine](https://github.com/unicorn-engine/unicorn)
2.1.4, tag commit `8028ec436f2d9376525352dd38ed9ed6b9f6be10`, applied in this
order, each against the tree with the ones before it applied:

- `unicorn-2.1.4-m68k-hook-ccr-sync.patch` touches
  `qemu/target/m68k/translate.c` and `qemu/target/m68k/unicorn.c`.
- `unicorn-2.1.4-m68k-emac-mac-load.patch` touches
  `qemu/target/m68k/translate.c`.
- `unicorn-2.1.4-m68k-emac-modes.patch` touches
  `qemu/target/m68k/helper.c` and `qemu/target/m68k/translate.c`.
- `unicorn-2.1.4-m68k-fast-mem.patch` touches `qemu/accel/tcg/cputlb.c`,
  `qemu/accel/tcg/translate-all.c` and `.h`, `qemu/include/tcg/tcg.h`,
  `include/uc_priv.h` and `uc.c`.
- `unicorn-2.1.4-m68k-digikit-accel.patch` touches
  `qemu/target/m68k/cpu.c` and appends to `uc.c`.

The first three are correctness fixes, and the emulator refuses to run
without them. The last two are speed: they are what make live audio on the
mk1 run faster than real time. Without them the emulator takes the same code
paths in Python, with byte-identical audio but about nine times slower: two
emulated seconds of 48 kHz audio from the loaded-sample snapshot took 11.3 s
of wall time instead of 1.26 s (WSL, 2026-09-23).

Everything except the `uc.c` additions is QEMU source vendored inside
Unicorn. **The patches are
derivative works of that code and carry its licence, not this repository's
choice of one** — take their terms from Unicorn and QEMU upstream. This is why
the repository as a whole is GPL-2.0-or-later rather than something
permissive; see the licence section of the top-level [README](../README.md).

## What the CCR patch fixes

Unicorn's m68k translator keeps condition codes lazily and commits them when a
translation block ends normally. A code hook, or a `count=` stop, can return to
the host *mid-block*, before that commit — so a guest `CMP` followed by a
hook-visible `SR` read, or by a counted stop on the next instruction, exposes
stale flags. Guest branches then take the wrong arm.

The patch commits the pending condition codes before handing control back.

## What the EMAC patch fixes

`DISAS_INSN(mac)` handles MAC and MSAC with load (ColdFire Programmer's
Reference Manual, p.6-3 to 6-5 and p.6-22 to 6-23) wrongly in four ways.
QEMU master had the same code on 2026-09-15.

- It takes extension-word bits 1..0 as a dual-accumulate request and, on a
  core without EMAC_B such as the CFV4E, raises an illegal-instruction
  exception. In the load forms those bits are part of Ry, so every Ry whose
  register number has bit 0 or 1 set faulted. The patch honours the request
  only on EMAC_B cores.
- It reads a data-register Rx from operation-word bits 14..12, which are
  always 2 for these opcodes, so Rx was always D2. Rx is extension-word bits
  15..12.
- It reads the MSAC bit from operation-word bit 8, which is always 0 for
  these opcodes, so MSAC added. The bit is extension-word bit 8. This also
  affects MSAC without load.
- It ANDs MASK into every load address. Extension-word bit 5 says whether
  MASK is used.

The Digitakt II SHARC frame build reaches one of these instructions at
`0x400db9e0`.

## What the EMAC modes patch fixes

The EMAC's arithmetic outside plain signed-integer-with-positive-operands,
checked against the MCF5441x reference manual (5.2.1 MACSR, Table 5-3, the
5.3.5 pseudo-code). Digitakt mk1's audio engine computes at MACSR `0xA0`
(signed fractional, saturating) and mixes at `0x80` (signed integer,
saturating); with stock Unicorn it rendered silence, and with only the
fractional half fixed it rendered the negative half-wave only.

- Fractional products were unsigned and missing the `<< 1` that aligns the
  binary point, so 0.970 x 0.5 gave 0.2425 and every gain collapsed toward
  zero. Now signed, rounded (R/T) or truncated to 40 bits, `-1 x -1`
  zero-filled.
- The fractional store shifted the accumulator logically: with OMC every
  negative value was stored as 0. Now arithmetic, with 16-bit rounding for
  S/U and saturation that follows the sign.
- MACSR[S/U] was backwards in integer mode: clear is signed, set is unsigned
  (Table 5-3). Word operands, products, saturation, stores, loads and
  extension words all chose by it.
- The signed product was computed unsigned; signed accumulation overflow and
  the signed store saturated in the wrong direction; a signed `>>1` scale
  shifted logically.
- Changing MACSR[F/I,S/U] repacked the accumulators by the old mode, so a
  mode change converted nothing.
- EV used bit 40/32 boundaries; the manual's are ACC[47:39] and ACC[47:31].

Still not modelled, as before: the product/accumulation-overflow skip that
freezes a saturated accumulator until PAVn is cleared.

## What the fast-memory patch changes

Stock Unicorn sends every guest load and store through the slow C helper as
soon as any memory hook exists, and the emulator always has one (the global
`UC_HOOK_MEM_INVALID` demand mapper). On top of that the dirty-page bitmap is
a stub, so every store also ran the self-modifying-code check. Together that
made memory access the largest single cost of the mk1 audio render.

- Only pages a read or write hook covers take the slow path. They are marked
  per page in the TLB (`TLB_WATCHPOINT`, which is otherwise a no-op in
  Unicorn); every other page keeps QEMU's inline fast path.
- A page is cleaned for fast stores when it holds no translated code, and
  re-armed when code is translated on it (`tlb_protect_code`), so
  self-modifying code is still caught.
- Adding or deleting a memory hook refills the TLB. From inside a hook that
  is deferred to the next top-level `uc_emu_start`, because refilling mid-access
  frees the entry in use.

`tests/test_unicorn_fastmem.py` checks hook routing (including hooks added
after a run, deleted hooks, global hooks and a global invalid-memory hook) and
self-modifying code across and between translation blocks.

## What the digikit accelerator patch adds

- The CFV4E gets `M68K_FEATURE_CF_ISA_APLUSC`. The MCF5441x is an ISA_C core
  (MCF54418RM 3.3.2, Table 3-4), so FF1, BITREV and BYTEREV are real
  instructions; stock Unicorn raised illegal-instruction for them and the
  emulator emulated FF1 from a hook. `emu/harness.py` skips that hook when the
  library has FF1 natively.
- `uc_digikit_add_budget_hook`: ends `emu_start` after N translation blocks
  without a Python call per block (the fast stepper in `emu/longrun.py`).
- `uc_digikit_edma_install`: the software-started eDMA channels the mk1 audio
  render starts and polls, done natively. `emu/edma_sw.py` is the Python
  original and states the rules; `emu/native.py` holds the ctypes mirrors and
  checks their size against `uc_digikit_edma_size()`.

`emu/native.py` probes for both exports and the emulator falls back to the
Python paths when they are missing.

## Checks

`emu/unicorn_compat.py` exercises the CCR shapes, the `0x400db9e0`
instruction and fractional and signed-integer MACs, and refuses to run on an
interpreter whose Unicorn lacks any of the patches, rather than letting a
subtly wrong emulation pass for a working one. `tests/test_unicorn_emac.py`
checks the MAC and MSAC load forms, fractional and integer modes and mode
switches against the manual.

## Applying them

Do not apply these by hand. `tools/install-patched-unicorn.sh` (or its
Windows twin, `tools/install-patched-unicorn.ps1`) pins the upstream commit, verifies each patch's SHA-256, builds only the m68k target,
clones from a local repository instead of GitHub when given one
(`UNICORN_GIT=PATH` / `-Source PATH`),
replaces the dynamic library the Python bindings actually load, and then runs
the compat check. `uv sync` can restore the stock wheel, which puts the
emulator back to refusing to start until the installer is rerun.
