# Digitakt (mk1) emulator, derived from digikit

An emulator for the Elektron Digitakt (mk1), OS 1.53. The ColdFire control
processor boots to its live user interface, plays audio at 48 kHz in real
time, runs patterns, and loads samples from an emulated +Drive, all driven
from a clickable front panel. Not affiliated with or endorsed by Elektron.

Derived from [m-dwyer/digikit](https://github.com/m-dwyer/digikit)
(GPL-2.0-or-later) at upstream commit `a5643ba`, published as one snapshot
rather than with its history.

**Not included:** Elektron firmware (you supply your own `.syx`), anything
extracted from it (sections, snapshots, card images), and any tooling for
building, signing or patching firmware images.

Upstream targets Digitakt II and Digitone II. Those paths still work: every
mk1 behaviour is selected by the device file (`devices/digitakt.toml`), and a
device that says nothing keeps upstream's behaviour.

Target firmware: **Digitakt mk1 OS 1.53**, SHA-256
`9bdd44bb6102fb25c143cfab97bc92b7a89c463f795d3112dce89771e29bcc92`.

## Windows app: digiemu

`digiemu-win64-<version>.zip` is a portable folder: unzip it anywhere you can
write (not inside OneDrive), run `digiemu.exe`, and pick **Add firmware** to
give it your own `.syx`. The app reads the device and version from the file
itself. It then builds everything the emulator needs, once, in under half a
minute on a desktop (about a minute on a slow laptop): it extracts the
firmware, creates and formats a +Drive card, and cold-boots until the
firmware has installed its factory project and sounds onto that card. After
that, **Play** opens the panel straight away. Quitting saves the session, so
the next Play carries on where you left off.

Everything lives next to the exe, in `firmware\<name>\`: your `.syx`, the
card image and the snapshots. Move or copy the folder freely, but never share
what is inside `firmware\`, which is derived from your firmware. **Rebuild**
boots again from the card as it is now; **Reset to factory** starts over with
an empty card.

- Tested with Digitakt mk1 OS 1.53. Another Digitakt mk1 release is offered
  as "untested" and runs after you confirm; other Elektron products are
  recognised but not supported yet.
- The firmware's factory *sample* library lives on a real device's storage,
  not in the firmware, so `/factory` is empty and tracks that use those
  samples are silent.
- The exe is unsigned: Windows shows a SmartScreen prompt on first run, and
  PCs with Smart App Control on block it. Its Control Flow Guard flag is
  cleared, because Unicorn's longjmp fails under it; the process then runs
  with the same protections as `python.exe`.
- `digiemu-console.exe --add SYX | --list | --rebuild NAME | --reset NAME
  --yes` does the same without the window, so a CI job can set up a custom
  firmware build in one step (~23 s on the reference desktop; ~13 s for a
  rebuild over a card that already holds the factory content). Logs are in
  `firmware\<name>\logs\`.

Build the zip with `tools\build-windows.ps1` (offline; see its header and
`packaging/`). From source, `python -m emu.portable` runs the same app.
digiemu is the app's own name; the emulator inside it is this fork of
digikit.

## Setup from source

You need Python 3.12 ([uv](https://docs.astral.sh/uv/)), a C toolchain for
the patched Unicorn ([docs/UNICORN.md](docs/UNICORN.md)), and your own
`Digitakt_OS1.53.syx` in the working directory.

```sh
uv sync
tools/install-patched-unicorn.sh     # Windows: tools\install-patched-unicorn.ps1
uv run python -m emu.run Digitakt_OS1.53.syx
```

The first run extracts the firmware and builds the boot snapshots, which
takes a few minutes, once. Then make the fast-start snapshot:

```sh
uv run python tools/introboot.py --syx Digitakt_OS1.53.syx \
    --snapshot snapshots/Digitakt_OS1.53/boot400M.snap \
    --min-lit 1200 --out snapshots/Digitakt_OS1.53/gui-raw.snap
uv run python tools/uisettle.py --syx Digitakt_OS1.53.syx \
    --snapshot snapshots/Digitakt_OS1.53/gui-raw.snap \
    --out snapshots/Digitakt_OS1.53/gui.snap
```

Do not skip `uisettle.py`. At the first UI frame the firmware is still doing
its first-boot +Drive work, and a snapshot taken there replays it on every
start. (This bootstrap has not been re-run from scratch for mk1; the steps
after it are verified.)

## Run

```sh
uv run python -m emu.dtpanel [snapshot]
```

With no argument it opens `gui.snap`. Click a key to press it. Shift-click
latches a key, for combinations like FUNC + key, and Esc releases latched
keys. Turn a knob with the mouse wheel or by dragging. The header has MUTE,
PLAY, CLEAR and SAVE WAV for the audio; `--no-audio` skips the audio model.

To put samples on the +Drive, close the emulator (it keeps the card image
mapped) and add them to the image; they appear in `/incoming`:

```sh
uv run python tools/ekfsadd.py plusdrive.img kick.wav snare.wav
```


## Status

Working: boot to the live UI, every key and encoder with the key LEDs, live
48 kHz audio, the sequencer, and the +Drive. On a desktop the emulator can
run live audio about 2.5 times faster than real time; `tools/capbench.py`
measures a given machine.

Not yet: forced interrupts on the second interrupt controller and the
interrupt masks are not modelled, and 44.1 kHz samples are not resampled.
The handoff's "Open" list has the rest.

## Where things are

| | |
|---|---|
| [HANDOFF-2026-09-23.md](HANDOFF-2026-09-23.md) | Current state and next steps. Each handoff links the one before. |
| [DIGITAKT-MK1.md](DIGITAKT-MK1.md) | How the mk1 boot, panel, audio and sequencer work, and the tools |
| [docs/mk1/](docs/mk1/00-INDEX.md) | Firmware reference: 01–09 are generated, 10 onwards written by hand |
| [patches/README.md](patches/README.md) | The six Unicorn patches |
| [docs/TOOLS.md](docs/TOOLS.md) | Reverse-engineering tool index |
| [docs/UPSTREAM-README.md](docs/UPSTREAM-README.md) | Upstream's README, for Digitakt II and Digitone II |

Tests: `tools/ci/run-tests.sh` runs each test module on its own; tests that
need the firmware skip without it. `tools/livecheck.py --pattern` checks live
audio end to end.

## Licence

GPL-2.0-or-later ([LICENSE](LICENSE)). The patches in `patches/` modify QEMU
source vendored inside Unicorn, so they carry its licence, and the emulator
does not run without them.

The licence covers the code here and nothing else. **No Elektron firmware is
included**; it is copyright Elektron. Nothing here grants any right to
Elektron's software, and nothing here is legal advice.

Container-format knowledge derives from `mischa85/elektron-firmware-tool`
(MIT). Architecture and memory-map facts marked *Documented* in
`docs/FINDINGS.md` derive from `lalzart/digitakt-ii-firmware-research-public`
(MIT). MIT is GPL-compatible, so both carry forward under this licence.
