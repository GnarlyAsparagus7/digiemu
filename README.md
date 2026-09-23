# Digitakt (mk1) emulator, derived from digikit

**Start here: [HANDOFF-2026-09-23.md](HANDOFF-2026-09-23.md)** — live audio
in real time, and patterns playing. It continues
[HANDOFF-2026-09-22.md](HANDOFF-2026-09-22.md) (the +Drive, sample loading,
NO, the key LEDs), which continues
[HANDOFF-2026-09-21.md](HANDOFF-2026-09-21.md): the state, the roadblocks, and
the traps that cost real time, including the theories already tested and
killed, so they are not re-derived.
[HANDOFF-2026-09-20.md](HANDOFF-2026-09-20.md) still holds the boot-intro and
panel work in detail.

Then [DIGITAKT-MK1.md](DIGITAKT-MK1.md) for how the mk1 boot actually works
and what each tool is for.

This is derived from [m-dwyer/digikit](https://github.com/m-dwyer/digikit)
(GPL-2.0-or-later) at upstream commit `a5643ba` (2026-09-18), published as a
single snapshot rather than with its history. Files throughout have been
modified since then; the handoffs and `DIGITAKT-MK1.md` describe what changed
and why. Upstream targets Digitakt II / Digitone II; every mk1 behaviour is
selected by the device file, and a device that says nothing keeps upstream's
behaviour.

**What is deliberately not here:** any Elektron firmware (you supply your own
`.syx`), anything extracted from it (sections, snapshots, card images), and
any tooling for building, signing or patching firmware images. This is an
emulator and a set of research notes about how the firmware works, not a way
to change what a device runs.


Target: **Digitakt mk1, OS 1.53**, sha256
`9bdd44bb6102fb25c143cfab97bc92b7a89c463f795d3112dce89771e29bcc92`.

## What this fork adds

- **mk1 boots to its live user interface**, in 100M instructions from a ladder
  rung. mk1 and Digitakt II need *opposite* intro handling: DT2 holds every
  PIT and lets `unblock` satisfy the frame semaphore, while mk1's intro parks
  on that semaphore and only a real PIT3 tick can post it. See the handoff.
- **A Digitakt-shaped GUI** (`emu/dtpanel.py`) with genuine multitouch —
  shift-click latches, because the panel protocol is a per-channel button
  bitmask.
- **A measured 48-key panel map** (`emu/dtmap.py`). Upstream's
  `channel*8+bit+1` arithmetic is false on this panel.
- **~16 measurement tools** under `tools/`, including `m68dis.py` (a capstone
  m68k disassembler) and `hotspots.py` (block-entry profiler).
- Several upstream bug fixes found while porting.

## Setup

**No firmware is in this repo and none ever should be** — that is upstream's
policy and this fork keeps it. `.gitignore` excludes the `.syx`, the extracted
sections and every snapshot. Supply your own lawfully-obtained copy.

```sh
uv sync
tools/install-patched-unicorn.sh
uv run python -m emu.run Digitakt_OS1.53.syx
```

That extracts the sections and builds the boot ladder on first run (a few
minutes, once). Then build the fast-start snapshot and open the panel:

```sh
# 1. boot to the first frame of the main UI
uv run python tools/introboot.py --syx Digitakt_OS1.53.syx \
    --snapshot snapshots/Digitakt_OS1.53/boot400M.snap \
    --min-lit 1200 --out snapshots/Digitakt_OS1.53/gui-raw.snap

# 2. run on until the UI has SETTLED, and save THAT as gui.snap
uv run python tools/uisettle.py --syx Digitakt_OS1.53.syx \
    --snapshot snapshots/Digitakt_OS1.53/gui-raw.snap \
    --out snapshots/Digitakt_OS1.53/gui.snap

uv run python -m emu.dtpanel
```

**Do not skip step 2.** `introboot.py` stops at the first frame of the main
UI, and the firmware is still doing its +Drive first-boot work at that
moment -- it finishes roughly 400M instructions later. A snapshot saved at
step 1 replays the tail of that work on every resume, which shows up as the
screen flashing "FACTORY PROJECT >> +DRIVE..." over the main page forever.
`uisettle.py` waits for 120 consecutive frames with no progress overlay
before it saves.

## Putting a sample on the +Drive

Drag files onto **`plusdrive-drop.cmd`** in the Windows tree. They land in
`/incoming` on the card image the emulator uses, which is the directory the
firmware itself uses for samples transferred from a computer. Close the
emulator first: it keeps the image memory-mapped while it runs.

From a shell it is:

```sh
uv run python tools/ekfsadd.py plusdrive.img kick.wav snare.wav
uv run python tools/ekfsadd.py plusdrive.img --check      # verify only
uv run python tools/ekfsadd.py blank.img --format         # new filesystem
```

`--format` only touches the sample region, which starts at sector `0x1c0000`.
What the tool knows about the filesystem was read out of the firmware and
checked against an image the device formatted: the superblock checksum is
reproduced exactly, and a filesystem built by `--format` matches the
device's byte for byte. See `docs/mk1/10-plusdrive.md`.

`emu/run.py:paths_for` prefers `gui.snap` when it exists, so `emu.dtpanel`
with no arguments then opens straight onto the running interface. Delete
`gui.snap` and everything still works from the ladder — it is purely an
optimisation.

Honest caveat: the bootstrap above has **not** been re-run from scratch for
mk1 in this fork — the sections and ladder already existed in the working copy
this was staged from. The mk1-specific steps after it are verified.

## Known limits

The sequencer does not run and this is not a missing clock; the per-tick
position commit is audio-side. Audio is not on the ColdFire at all (zero
SSI0/SSI1 references in MAIN OS) — it lives on a coprocessor whose blob is ARM
Thumb-2 on mk1, so upstream's SHARC support does not transfer. The UI blocker
and the audio blocker are one blocker.

---

*Upstream's README follows.*

# Digitakt II firmware research

An emulator for the Elektron Digitakt II's control processor, plus tooling for
understanding its OS images.

**No Elektron firmware is included in this repository and none ever should
be** — it is copyright Elektron. You supply your own lawfully-obtained `.syx`;
`.gitignore` keeps it, the extracted sections and the snapshots out of git.

**Only `Digitakt_II_OS1.15C.syx` has been tested**, SHA-256
`62d588456e47194bd56dfee9568fb9dd4521c4ff1e8b5427eb461355532e8c6c`. Every
address in this repo and its documentation is specific to that build. A
different firmware version will almost certainly not boot, and nothing here
tries to detect that for you.

## What it does

It boots. The main OS runs, spawns its RTOS tasks, reaches its message loop and
**renders its user interface** — pattern and project name, tempo, the encoder
parameter row, the sample page with its knob widgets — into a 128x64 panel you
can watch live, or dump to a PNG with `emu.panel`.

It is a research instrument, not a Digitakt you can play, but it is no longer
too slow to watch. Block-bounded stepping reaches roughly 10M instructions a
second — ahead of the 4.68M the timer models pace the firmware's own clock to,
though still far short of the real part's ~264M — so the panel renders at
20–25 fps and the GUI spends the surplus sleeping, keeping the emulated clock
and the wall clock together. See **What works, and what does not** below.

## Quick start

You need Python 3.12 (via [uv](https://docs.astral.sh/uv/)) and your own
firmware file in the working directory.

```sh
uv sync
tools/install-patched-unicorn.sh   # Windows: tools\install-patched-unicorn.ps1

# Digitakt II
uv run python -m emu.run Digitakt_II_OS1.15C.syx

# Digitone II
uv run python -m emu.run Digitone_II_OS1.10E.syx
```

That checks each prerequisite, builds the boot snapshots on first run (one cold
boot from reset, a few minutes — it happens once), and opens the live panel.

**No flags are needed.** Three you will see in older notes are not:

- `--slc` forces the eMMC SLC flag, which the eSDHC storage model already
  supplies. `build()` turns that model on by default.
- `--unthrottled` used to be how you got a watchable frame rate. It is now the
  wrong choice: the emulator outruns the firmware's clock, so without the flag
  the GUI paces itself to real time and the sequencer keeps proper tempo, and
  with it everything runs about 1.5x too fast.
- `--weakptr` steps over the weak_ptr branches that used to freeze the main
  task. It is a fallback for a boot that stalls, not a default.

`--exact` is worth knowing about: it swaps block-bounded stepping for exact
`count=` stepping, about 5x slower, and is what to use when comparing a run
against `tools/bootcheck.py`.

**One firmware at a time.** `sections/` holds the decompressed image of
whichever `.syx` was extracted last, and the filenames are fixed, so switching
between the two devices re-extracts. `emu.run` checks this and refuses rather
than running one firmware under the other's name; do what it tells you. To run
one build while another occupies `sections/`, point `DT2_SECTIONS` at a second
directory instead.

Unicorn needs the repo's patches (SR read and CCR sync, EMAC MAC with load);
see [docs/UNICORN.md](docs/UNICORN.md).
`uv sync` can restore stock Unicorn, which the emulator deliberately rejects
until the installer is rerun.

That includes decompressing the sections out of the `.syx`, which no longer
needs an outside tool. To do it on its own:

```sh
uv run python -m emu.extract Digitakt_II_OS1.15C.syx -o sections/
```

The decompressor is `dt2/elz.py`, a byte-level decoder for the device's codec
written from the format in
[elektron-firmware-tool](https://github.com/mischa85/elektron-firmware-tool)'s
`aplib.c`. It reads every firmware in the repo root, 1.16 and 1.11 included.
`--oracle` uses the device's own routine instead: section 4 is the *updater*,
it is stored **raw**, and an updater has to unpack the image it installs, so
it carries its own copy of the depacker; `emu/extract.py --oracle` runs that
copy under Unicorn. Both are byte-identical to `emu.oracle.depack` — the
device's own section-2 routine — for every compressed section of Digitakt II
1.15C and Digitone II 1.10E. The oracle cannot read 1.16 or 1.11.

### Options

```sh
uv run python -m emu.run [firmware.syx] [snapshot] [options]
```

| | |
| --- | --- |
| `--weakptr` | Step over two branches in `weak_ptr::lock` that otherwise freeze the main task after 153 messages. They contradict the memory they branch on — an emulator defect, not a firmware decision. Recommended. |
| `--slc` | Force the eMMC "SLC mode" flag. Unnecessary now that the eSDHC model supplies it. |
| `--scale N` | Integer panel zoom. Defaults to whatever fits your screen. |
| `--check` | Resolve and validate everything, then stop without running. |
| `--accept-sections` | Confirm the extracted sections really are the firmware you named. |

Nothing is hardcoded to a filename. Paths resolve from an explicit argument,
then an environment variable, then discovery:

| | |
| --- | --- |
| `DT2_SYX` | the firmware `.syx` |
| `DT2_SECTIONS` | directory of extracted sections (default `sections`) |
| `DT2_SNAPSHOTS` | directory of boot snapshots (default `snapshots`) |
| `DT2_MAIN_IMG` | the decompressed MAIN OS image |

The section filenames are fixed, so `sections/` holds exactly one firmware at a
time. `emu.run` records which `.syx` it came from and refuses to pair it with a
different one — otherwise a second firmware silently runs against the first
one's code. Snapshots for anything other than the tested build go in their own
subdirectory for the same reason.

## What works, and what does not

**Working:** the ColdFire core, the interrupt controllers, the PIT and DMA
timers, eDMA, the front-panel link, the coprocessor port's handshake, the
display, and the SD/MMC controller through card identification.

**Storage is not supported yet.** The eSDHC controller and an eMMC are modelled
far enough to complete card identification and read EXT_CSD, but **no block
data is served** — `CMD18` reads return zeros. Bulk transfers move through the
SoC's eDMA with `SADDR = DATPORT`, and nothing backs them. So the firmware
boots and draws, but cannot load a project or samples, and anything that
touches the filesystem will not work. Backing it with a real image, and then
generating one from a folder of samples, is the next substantial piece of work.

**No audio.** The device makes sound on a second processor — an Analog Devices
ADSP-21569 SHARC+ running its own firmware. Nothing here emulates it. The main
OS holds parameter state and RPCs it to the SHARC; that split is what makes
audio a separate project rather than a missing feature.

**No input.** The front-panel protocol is decoded in the transmit direction
only. Buttons and encoders arrive on the receive side, which is not modelled,
so the UI cannot be driven.

## The hardware

Not ARM. Two processors:

| | Part | Notes |
| --- | --- | --- |
| Control | Freescale **ColdFire MCF54415** | 68k-family ISA, **big-endian**. UI, sequencer, files, MIDI. C++. |
| Audio | Analog Devices **ADSP-21569** SHARC+ | FreeRTOS. Shipped as ADI loader records. |

## Layout

```
dt2/container.py   .syx -> 8-in-7 decode -> ELE3 container + section table
dt2/coldfire.py    ColdFire-aware disassembly (Capstone misses MVS/MVZ and FF1)
emu/harness.py     Unicorn m68k machine; works around four Unicorn/ColdFire gaps
emu/longrun.py     build() -- the machine, its models and every opt-in switch
emu/run.py         .syx -> running emulator, one command
emu/gui.py         live panel
emu/panel.py       the framebuffer the firmware actually draws into
emu/esdhc.py       SD/MMC controller + eMMC (identification only)
emu/oracle.py      runs the DEVICE'S OWN validators against a candidate image
docs/HANDOVER.md   current state, what to do next, and the traps that cost time
docs/TOOLS.md      reverse-engineering tool index and workflow guide
```

## Other tools

See **[docs/TOOLS.md](docs/TOOLS.md)** for the full tool index, including the
Ghidra, SHARC+ loader/decoder, targeted data-flow and measurement workflows.

```sh
uv run python -m dt2.container Digitakt_II_OS1.15C.syx   # section table
uv run python -m emu.panel <snap> <instrs> 3 out.png     # render the panel
uv run python -m emu.uiprobe sweep                       # the measurement sweep
uv run python -m emu.tasks <snap>                        # parked PC per task
uv run python emu/oracle.py                              # acceptance oracle
```

## Continuing this work

**Read [`docs/HANDOVER.md`](docs/HANDOVER.md) first.** It is written for someone
with no memory of the sessions that produced this, and it opens with six
standing warnings about measurements that have already misled people — several
of them cost a whole session each. [`docs/NEXT.md`](docs/NEXT.md) is the
overview and [`docs/FINDINGS.md`](docs/FINDINGS.md) the older evidence.

## The device-routine oracle

`emu/oracle.py` runs two of the bootstrap's own routines under emulation, the
CRC-32 at `0x80001bd0` and the section depacker at `0x80000432`, each
verified byte-exact. `emu/extract.py --oracle` uses the depacker as an
independent check on `dt2/elz.py`.

## Licence and attribution

**GPL-2.0-or-later.** See [LICENSE](LICENSE).

This said MIT until 2026-09-13, and could not:
the patches in `patches/` modify `qemu/target/m68k/translate.c` and
`qemu/target/m68k/unicorn.c` — QEMU source vendored inside Unicorn — so they
are derivatives of that code and carry its terms. The dependency is not
incidental either: `emu.unicorn_compat` refuses to run against stock Unicorn,
so nothing here works except against the patched build. GPL-2.0-or-later is the licence that costs nothing to be right about.

The licence covers the code in this repository and nothing else. **No Elektron
firmware is included and none ever should be** — it is copyright Elektron, and
the `.syx` you run is yours to supply. Nothing here grants any right to
Elektron's software, and nothing here is legal advice.

Not affiliated with or endorsed by Elektron. Container format knowledge derives
from `mischa85/elektron-firmware-tool` (MIT); architecture and memory-map facts
marked *Documented* in FINDINGS.md derive from
`lalzart/digitakt-ii-firmware-research-public`. MIT is GPL-compatible, so both
carry forward under this licence.
