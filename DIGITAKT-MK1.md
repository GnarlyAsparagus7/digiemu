# Digitakt mk1 emulator — dev suite

Emulates Digitakt (mk1) OS 1.53 on a ColdFire MCF5441x, via a port of
[digikit](https://github.com/m-dwyer/digikit) (written for Digitakt II /
Digitone II). Everything lives in WSL at `/root/digitakt/digikit`.

## Launch

```bash
wsl -d Ubuntu -- bash -c "cd /root/digitakt/digikit && .venv/bin/python -m emu.dtpanel"
```

The window opens onto the running user interface **immediately** — it restores
`snapshots/Digitakt_OS1.53/gui.snap`, a snapshot of the firmware already at
its main screen. With the patched Unicorn it runs at 64M counted
instructions a second, in real time, with live audio (see Audio below);
without the fast-memory and accelerator patches, at roughly 8–11M, about half
of real time, with audio rendered and played back afterwards.

Plain click = momentary press. **Shift-click latches**, so chords like
`FUNC` + a trig are held together — the panel wire protocol is a per-channel
button bitmask, so this is real multitouch, not a modifier hack. `Esc` clears
all latched keys.

To boot from cold instead (plays the intro, ~100M instructions):

```bash
wsl -d Ubuntu -- bash -c "cd /root/digitakt/digikit && rm snapshots/Digitakt_OS1.53/gui.snap && .venv/bin/python -m emu.dtpanel"
```

## How the boot actually works

This is the part that is specific to mk1 and cost the most to find, so it is
worth stating plainly: **mk1 and Digitakt II want opposite things from the
boot intro.**

| | Digitakt II | Digitakt mk1 |
|---|---|---|
| PITs during the intro | hold all | **deliver PIT3** |
| frame semaphore | let `unblock` satisfy it | **leave it alone** |

DT2's intro is driven by `unblock` force-satisfying its frame semaphore, so a
real PIT3 tick would post that semaphore a second time and the draw loop would
never reach its exit test. mk1's intro instead *parks* on the semaphore, and
`unblock` only ever sees a pend on the way **in** — it can never satisfy a wait
that is already blocked. The PIT3 tick is the only thing that can post it.

Measured from `boot400M.snap`, all three cases:

| policy | result |
|---|---|
| hold everything | nothing at all for 600M instructions |
| deliver PIT3 **and** fake the semaphore | intro exits after 77 ticks instead of 180 frames; no OS task runs |
| deliver PIT3, semaphore left alone | **live UI in 100M instructions** |

The policy is per product, in `devices/digitakt.toml`:

```toml
[intro]
channels = [3]             # PIT channels to DELIVER while the intro owns vector 208
unblocks_frame_sem = false
```

The defaults (`channels = []`, `unblocks_frame_sem = true`) are the Digitakt II
behaviour, so device files that say nothing are unaffected.

### mk1 intro symbols

| symbol | mk1 | how it was established |
|---|---|---|
| `intro_pit3_isr` | `0x4006c154` | vector 208 at every ladder rung with PIT3 enabled; `0x400e5cec` (display module) after the intro |
| `intro_done` | `0x4006cb92` | `addq.l #4,a7` opening the exit sequence; hit exactly once, on the tick that switches PIT3 off |
| `frame_sem` | `0x41988be4` | `intro_done+4` operand minus 8 — the existing rule, unchanged |
| `display_sem` | `0x421cd06c` | falls out with the above |

`intro_done` anchors on the `addq.l #4,a7` at `0x4006cb92` rather than the
`pea` at `0x4006cb94` **on purpose**: mk1 has no leading `clr.w d0`, so
anchoring two bytes earlier is what keeps the shared
`Operand('intro_done', at=4, adjust=-8)` rule landing on the `pea` operand.
`0x4006cb8e` and `0x4006cb90` never execute.

One unresolved signature was holding down nine symbols, because `frame_sem` is
an `Operand` on `intro_done` and `intro_pit3_isr` is narrowed by `frame_sem`.
Unresolved went 18 → 9.

## Tools

Run from `/root/digitakt/digikit` with `.venv/bin/python`.

| tool | what it does |
|---|---|
| `tools/introboot.py` | boot a ladder rung to a live UI and save it (`--out snapshots/<stem>/gui.snap`) |
| `tools/introtrace.py` | per-chunk PCSR / lit / vector, plus hit counts for every site that touches PIT3 |
| `tools/introsweep.py` | sweep PIT channel sets against a rung — how the table above was produced |
| `tools/introfind.py` | read the PIT3 vector slot out of each snapshot; find candidate handler prologues |
| `tools/rungcheck.py` | which ladder rungs are resumable (intro live, not parked on the frame semaphore) |
| `tools/guipress.py` | press keys against `gui.snap` (the GUI's own flags) and capture each screen |
| `tools/dtdrive.py` | headless press-and-capture, against `ui.snap` |
| `tools/calib.py`, `tools/mapsweep.py` | identify controls by measured effect, not by label |
| `tools/ekfs.py` | read the +Drive filesystem out of a card image |
| `tools/ekfsadd.py` | write files into it, and `--format` a blank card |
| `tools/plusdrop.sh` | the WSL half of the Windows Explorer drop target |
| `tools/menusweep.py` | press every code with a menu open and judge by page SET, not by one frame |
| `tools/emucheck.py` | is this RUN trustworthy: one UI page, main loop advancing, +Drive mounted, no faked pend that had a real poster |
| `tools/symaudit.py` | every symbol resolves, and `AnyOf` alternatives agree; `tests/test_symaudit.py` is the same checks |
| `tools/livecheck.py` | live audio through the GUI's emulator thread with a stub sound card; `--pattern` records a pattern and checks every beat |
| `tools/capbench.py` | how far faster than real time live audio can run (unpaced, the livecheck pattern); A/B in alternating runs |
| `emu/tasks.py` | dump every TCB's parked PC and the ready list |
| `/root/digitakt/shot.sh` | screenshot the GUI under Xvfb: `shot.sh <secs> <out.png> [snapshot]` |

Regenerate the fast-start snapshot:

```bash
wsl -d Ubuntu -- bash -c "cd /root/digitakt/digikit && .venv/bin/python tools/introboot.py --syx Digitakt_OS1.53.syx --snapshot snapshots/Digitakt_OS1.53/boot400M.snap --min-lit 1200 --out snapshots/Digitakt_OS1.53/gui.snap"
```


## The INTC mask registers are indexed, and we ignore them

**Verified on this firmware.** Upstream digikit recorded, from U-Boot and MQX
sources, that on MCF5441x `SIMR`/`CIMR` are *indexed* rather than bitmasks --
a byte write of `x` sets or clears the single bit for source `x & 0x3f` inside
`IMRH`/`IMRL` -- and flagged it explicitly as not checked against firmware.
It is true here, and four independent facts settle it:

- **`IMRH` and `IMRL` are never written. Zero times, in any form.** Searching
  the image for their address constants across all three controllers returns
  0/0/0. The three bare controller bases also appear zero times, which closes
  the "load base into a register and index by offset" route a constant search
  would otherwise miss. `SIMR` appears 11 times and `CIMR` 24.
- **The firmware uses both conventions correctly in the same register block.**
  `INTFRCH`/`INTFRCL` at +0x10/+0x14 are driven as genuine 32-bit bitmasks
  (`bset.b #$d,d0` then a longword store); `SIMR`/`CIMR` are driven as a
  `move.b` of a small literal. It plainly knows the difference.
- **The literals only make sense as indices.** `move.b #$1d,(a0)` with
  `a0 = 0xFC05001D` sits two instructions after `ICR29 <- 6` and beside
  `vector_slot[221] <- handler`. As an index that is source 29, which is what
  everything around it says; as a bitmask it would clear bits 0, 2, 3 and 4.
- **`SIMR <- 0x40` on all three controllers during early boot** is U-Boot's
  `INTC_SIMR_ALL` mask-everything idiom (bit 6 = "all 64 sources"). As a
  bitmask it would be "mask only source 6", three times, for no reason.

Fourteen `CIMR` sites are corroborated twice over, by the `ICR` byte and the
vector-table slot written in the same basic block -- e.g. `0x400E25D8` writes
`CIMR <- 0`, `ICR00 <- 3` and `vector_slot[192] <- 0x400E1EB0`, and 192 is
INTC2 source 0 under `vector = 64 + 64*controller + source`, which
`docs/mk1/04-vectors.md` already classifies independently as storage DMA
completion.

**We model none of it, and the consequence runs the opposite way to the
worry.** The INTC region is ordinary zero-filled memory in `emu/harness.py`,
so `SIMR`/`CIMR` writes land as inert bytes nothing reads. `emu/pit.py`'s
`interrupt_level` and `emu/dtim.py`'s `Dtims.level` compute their mask term
from `IMRH`/`IMRL` -- words this firmware never writes -- so that term is
always 0 and the gate is ICR-level-only. The fear was that unmasks would be
dropped and interrupts would never fire. In fact **masks** are dropped, so
anything with a nonzero ICR is permanently deliverable. Measured on
`gui.snap`: every `IMRH`/`IMRL` reads 0, INTC2's `SIMR` holds `0x10` -- the
firmware's most recent act was to mask PIT3's source 16 -- and
`interrupt_level(208)` still returns 3.

Nothing is visibly broken because every one of the `CIMR` unmasks is preceded
by an `ICR` level write in the same block, so the ICR write is an accidental
but faithful proxy for the unmask. The asymmetry is that `SIMR` masks are not
accompanied by `ICR <- 0`, so re-masking is invisible to us in a way
unmasking is not.

**Neither emulator workaround traces back to this.** `interrupt_level(192)`
already returns 3 on a live snapshot, so the eSDHC semaphores are posted from
host code not because the vector is masked but because nothing in `emu/` ever
raises 192: the storage path runs on eDMA channel 59 (the real ISR at
`0x400E1EB0` writes `CERQ <- 0x3b`) and `emu/edma.py` models only channel 35.
That is a missing interrupt *source*, not a dropped unmask. The hand-delivered
PIT3 is likewise an IPL tie-break and frame-semaphore question, and the two
places the firmware masks source 16 also write `PCSR3 <- 0`, which `Pits`
already honours.

So modelling this is a correctness improvement, not a fix for anything
currently blocked, and it would make the emulator **stricter**. The shape is
~30 lines in a new `emu/intc.py` hooking writes to `base+0x1C`/`+0x1D` and
maintaining the `IMRH`/`IMRL` words, exactly as `emu/edma.py` already does for
eDMA `SERQ`'s identical bit-6-means-all convention; `interrupt_level` and
`Dtims.level` would then need no change at all, because the word they read
would finally mean something. The catch is snapshots: every rung holds a
zeroed INTC page with no record of which sources were unmasked, so seeding the
reset value `0xFFFFFFFF` would flip the machine from "everything unmasked" to
"everything masked" and kill every timer. It needs the mask state replayed
from the ICR levels the snapshot does carry, or a flag until the ladder is
rebuilt cold.

**A separate gap in the same block, and this one can lose an interrupt.**
`INTFRCL2` software-forced interrupts are not modelled (`emu/ssi.py` handles
`INTFRCH1` bit 31 only), so an RTOS reschedule requested by `bset #13` on
`0xFC050014` never delivers. Today the emulator gets context switches only
from real PIT0 ticks and `longrun`'s own `idle_yield` injection.


## Two checks worth running before believing a run

**`tools/emucheck.py` asks a different question from `tools/bootcheck.py`.**
bootcheck answers "did the OS take over". emucheck answers "is this run
trustworthy", and the two can disagree -- a frame that only looks like
progress, a +Drive the firmware quietly refused to mount, and a pend list
showing `unblock` raced ahead of a real poster are each a false
`MAIN_OS_RUNNING`. All three have happened in this port. It resumes a rung,
runs a budget, and asserts: the run finished on its limit; a frame with at
least 15% of pixels lit is on screen; the main loop is still being entered;
the scheduler holds a runnable task; `_DAT_420edc50` is 1 and the inode
bitmap is non-empty; and nothing on `unblock_skip` got force-satisfied.

Two of those thresholds differ from upstream's and the reasons are worth
keeping. Upstream counts `len(ev['tasks'])`, which counts `task_create` going
by -- on a settled snapshot every task was created long before the snapshot
and that count is 0, which says nothing. `emu/tasks.py` walks the scheduler's
priority array instead, and since that array holds only RUNNABLE tasks (pend_b
unlinks anything that blocks) the honest floor is 1, not a census. And
upstream's "+Drive formatted" counts overlay bytes written to sector 0, which
would have been perfectly happy throughout the bug this port actually had: the
firmware read the card, rejected it as an unknown part, and skipped the mount,
leaving good bytes on a volume nothing had mounted.

The real liveness assertion is the main loop, measured at ~6.4 entries per
million instructions on the settled snapshot. The check's floor is one per ten
million -- two orders of magnitude below, which separates running from stopped
without pretending to judge speed. That is the signal the samples-folder
freeze was diagnosed with, and the one a wedged UI task takes down while
timers, task counts and the framebuffer all go on looking healthy.

Verified both ways: PASS on `gui.snap` (384 main-loop entries over 60M,
mounted, 6 inodes, nothing faked), FAIL with exit 1 on `boot200M.snap` run 20M
forward, where there is no UI page and no main loop yet. A check that has only
ever passed has not been tested.

**`tools/symaudit.py` looks for symbols that resolve to nothing, or to two
different things.** Upstream's report was that all four of their bugs in one
session were a `Fixed(<old address>)` silently resolving to `None` on a newer
build, and a +0x2e4 port is exactly where that should bite. It does not, here:
83 rules, one unresolved (`px_copy`, an optional accelerator), and every
`Fixed` alternative that still resolves agrees with the rule that won -- so
the fixed addresses in this tree are mk1 addresses, already re-derived, not
Digitakt II ones that happen to verify. Two DT2 leftovers (`ui_key_dispatch`,
`ui_tick_inc`) fail their verify loudly, which is the design working.

What it did turn up: 15 of the 21 resolving `Fixed` rules are backed by 7-10
verify bytes. Nothing is wrong today -- each agrees with its signature -- but a
short verify is weak evidence on a build it was not written for, and that is
the shape of the failure upstream hit. The tool prints them.

`emu/symbols.py` also gained upstream's `SigAt` (a masked signature anchored
at a fixed delta from an already-resolved neighbour), for the case where one
routine is compiled twice and both copies mask identically. **No rule needs it
on mk1**: every signature that fails here fails with zero matches, not two. It
is in the tree for when that changes, not because anything is using it.

## Two snapshots, two toolchains

- `gui.snap` — built with the GUI's flags (`unblock`/`softfloat`/`bitmap`/`dsp`)
  and `unblock_except=(frame_sem,)`. For `emu.dtpanel`. **It must be saved
  with `tools/uisettle.py`, not `tools/introboot.py`.** introboot stops at
  the first frame of the main UI, which is about 400M instructions before the
  +Drive first-boot work finishes; a snapshot taken there replays the tail of
  that work on every resume and the screen flashes
  "FACTORY PROJECT ▸▸ +DRIVE..." over the main page indefinitely. uisettle
  waits for 120 consecutive frames with no progress overlay.
- `ui.snap` — built with default flags. For the headless tools. **This one
  still comes from the pre-rebuild ladder**, so `emu/esdhc.py` corrects its
  card capacity on restore and prints a line saying so. `tools/uisnap.py` no longer
  reaches a live UI from either ladder: run against the old `boot400M.snap`
  as a control it reports `lit=367 mainloop=0` to its 900M budget and
  refuses to save, and the rebuilt rung does the same all the way to 2.5G. That is an open
  question about the tool, not the ladder. Only `tools/dtdrive.py` reads
  the file.

`unblock_except` is part of the checkpoint build manifest, so a snapshot saved
under one policy will not reopen under the other. That is the manifest doing
its job, not a bug. `softfloat` changes instruction counts, which is why the
headless measurement tools deliberately stay off it.

**`unblock` now derives its never-fake list from the image
(`emu/semscan.py`, ported from upstream).** `unblock` force-satisfies every
semaphore pend so a run does not sit waiting on hardware nothing here
emulates. That is wrong for any semaphore whose poster CAN run in the
emulator: faking the pend does not avoid an endless wait, it races ahead of a
poster that was going to run. `display_sem` was found that way, by chasing one
hang. `emu/semscan.py` generalises it -- enumerate every `give`/`give_b` call
site with a literal semaphore operand, work out whether it sits in task code
or inside an ISR, and never-fake the ones whose poster runs here. It reads the
image only, takes about 0.1 s, and needs no Ghidra.

It needed two changes to work on mk1, both recorded in the code:

- Upstream pins `give`/`give_b` at `0x4000148c`/`0x400014fc`. Those are the
  Digitakt II addresses. On mk1 the same bytes sit at `0x40001770`/
  `0x400017e0` -- the documented +0x2e4 RTOS shift, confirmed by searching for
  upstream's own 40-byte signatures, which match exactly once each. Scanning
  for the DT2 addresses on this image finds nothing and returns an empty set,
  which is indistinguishable from "this build has no task posters", so
  `emu/symbols.py` now resolves both with `Sig` rules and `never_fake_semaphores`
  takes them as arguments. `emu/longrun.py` says so on `ev` rather than
  silently scanning nothing if either fails to resolve.
- `modeled_vectors` tells the scan which interrupt vectors this build really
  raises, and so which ISR posts really happen. It defaults to PIT3 (208)
  alone. Widening it is not free: naming a vector that never fires marks its
  semaphores never-fake and the run then waits for a post that never comes,
  which presents as a hang, not as an error.

**What it found, and what it changed: those are different answers.** On this
image the scan returns four semaphores -- `frame_sem`, `display_sem`, and a
producer/consumer pair at `0x421b0df0`/`0x421b0df8`. The first two are exactly
what this port had already arrived at by hand, which is the useful part: an
independent, image-derived method reproduces the hand-kept list. The pair is
new.

Measured effect on mk1 so far: **none.** A/B across the boot from `boot60M`
with the scan live and with it stubbed to an empty set gives byte-identical
counts -- 4,311,672 faked pends in both arms, all of them on one semaphore
(`0x421a8d34`) that the scan correctly leaves fakeable. From `boot400M`, and
on the settled snapshot, nothing is faked at all in either arm. Nothing on any
path measured here ever pends the new pair, so protecting it is currently a
no-op. It is kept because the cost is 0.1 s at build time and the failure it
prevents is silent, but it should not be described as having fixed anything
here.

Upstream reports this change clearing the "FACTORY PROJECT >> +DRIVE..."
overlay freeze on Digitakt II 1.16. It did not do that here, because that
symptom had a different cause on mk1 -- the unmounted +Drive above -- and was
already gone.

**How to measure what a key does, because the obvious way is wrong.**
Three conclusions were drawn and retracted in one session by comparing ONE
frame before a press with ONE frame after. That fails two ways. On a snapshot
where the +Drive overlay alternates with the main page several times a
second, the sample is a coin toss between three pages, so almost every code
reads as live. On a settled snapshot the screen is static, so a key whose
effect appears and resolves inside the window reads as dead. The first error
said the settled snapshot had broken panel input; the second said `YES` does
nothing. Neither was true.

What holds: capture every untorn frame across a window before the press and
again after, and compare the SETS of lit-counts. And pair it with a control
that must pass -- `GLOBAL` opens the settings list, page 1534 appears, and a
second `GLOBAL` closes it and returns the set to the idle page alone. If that
control does not pass, nothing else measured in that run means anything.

**What that method says about the menu keys.** Opening the settings list and
pressing each of the 48 codes, one fresh resume per code:

| behaviour | codes |
|---|---|
| leaves the list, so confirm, cancel or a page jump | 22 of 47, including 12 (`YES`), 5, 7, 8, 13, 15 |
| changes something while the list is still up | 24, including all of 24..39 |
| nothing at all | 20 (`SRC`) |

So `YES` is not dead: code 12 leaves the list. The eight codes wired to
channel 1, which this file's own comments call the cursor cluster, all return
byte-identical page sets, which is what a key that never reaches the firmware
looks like -- so the arrows are still the doubtful part of the map, not the
confirm key.


**Loading the samples folder froze the UI. Fixed: the +Drive was never
mounted.** The screen stopped redrawing the instant the cursor reached the
SAMPLES row of SETTINGS, with the `mainloop` counter frozen while the PIT/DTIM
counts kept rising. It was not a stalled storage read. It was a crash.

The chain, each step measured rather than inferred:

1. `FUN_400ee3f2` is an empty `while (true) {}`. It is the firmware's abort
   handler -- about forty functions call it -- and the "frozen" UI was the UI
   task parked in it forever while interrupts carried on.
2. It was reached from the C++ unwinder (`FUN_401265dc` -> `FUN_40125484`),
   which could not start, so the throw became an abort.
3. The exception carried the message `basic_string::_S_construct null not
   valid`: something built a `std::string` from a NULL `char*`. The site is
   `FUN_4005135e`, which does `std::string(FUN_400d01c0(node))` to get a
   filesystem entry's name for the row it is about to draw.
4. `FUN_400d01c0` returns NULL when the node's validity virtual (vtable+0x30)
   says no, and that answer comes from `FUN_400d2402` testing a bit in the
   in-RAM inode bitmap at `0x426876b0`.
5. On a resumed `gui.snap` that bitmap was 8192 bytes of zeros and the
   mounted flag `_DAT_420edc50` was 0. Nothing was mounted, so every inode
   read back invalid, so every name was NULL. (This originally also called
   `0x426856b0` "the block bitmap" and counted its zeros as evidence. It is
   not: `FUN_400d2728` sets a bit there on open and `FUN_400d2750` clears it
   on close, so it is the open-files set, and all zeros only means no file
   was open.)

**Why it was never mounted.** `FUN_40068eb6` mounts only if the storage
bring-up left no error:

    if (_DAT_41980918 == 0 && FUN_400e2592() == 1) { ...; FUN_400d0f9c(0); }

`_DAT_41980918` was **7**, which is `FUN_400e2792` returning -2. That comes
from `FUN_400e253e`, a whitelist: it walks seven 32-byte rows at `0x4020c2c4`,
matching the CID's manufacturer id against word 0 and its six-character
product name against word 1. No manufacturer match is -1; a manufacturer match
with the wrong name is -2. The table is:

| manufacturer | names | EXT_CSD 156..158 / 222 / 227 | sectors MLC / SLC |
|---|---|---|---|
| 0x11 | `004GE0` `004G60` `004GA0` `004G90` | 0x1d8 / 1 / 8 | 0x760000 / 0x3b0000 |
| 0x15 | `4FTE4R` | 0xe9 / 0x10 / 1 | 0x748000 / 0x3a4000 |
| 0x70 | `TX2932` `TS0A32` | 0x4db / 0x10 / 1 | 0x3a4a000 / 0x2e1a000 |

`emu/esdhc.py` presented `cid = [0, 0, 0, 0x00110000]`: manufacturer 0x11,
which matched, and an all-zero product name, which did not. Exactly -2. The
firmware had been quietly rejecting the card on every boot since the eSDHC
model was written, and nothing said so -- an unmounted +Drive looks identical
to an empty one until something asks for a name.

**The fix** is in `emu/esdhc.py`: present a card the table accepts. `PART`
there holds the row's own values, read back out of the image, and `cid_words`
packs them into the R2 response. It also needed the three EXT_CSD identity
bytes `FUN_400e26f2` compares (156..158 as one 24-bit value, 222, 227; a
mismatch is -3) and the SLC sector count (a mismatch is -4) -- with SLC_OK 1,
which the mount also requires, the card must report 0x3b0000 sectors, not
0x760000. That is 1.98 GB, and with the +Drive region starting at sector
0x1c0000 it leaves ~1 GB of sample space, which is what the hardware claims.

One more change was needed: `Esdhc.__init__` defaulted to a blank in-memory
`Card()`, so the cold boot identified and mounted against an empty card and
only got the real image attached afterwards, on the resume. It now defaults to
the configured `DT2_PLUSDRIVE` image, the same one `emu/longrun.py` passes.

**Rebuilding is not optional.** A resume never re-runs either the card
identification or the mount. The identification is done inside the first 60M
instructions and the mount before the 280M rung, so every snapshot bakes in
whatever the cold boot saw -- which is why changing `emu/esdhc.py` and
resuming the old `gui.snap` changed nothing at all, and why the old
`boot60M.snap` still reported error 7 with the fix in place. The whole ladder
had to be cold-rebuilt:

```bash
wsl -d Ubuntu -- bash -c "cd /root/digitakt/digikit && DT2_PLUSDRIVE=/root/digitakt/usercard-live.img .venv/bin/python -m emu.checkpoint make 60000000,120000000,200000000,280000000,400000000 snapshots/Digitakt_OS1.53/boot Digitakt_OS1.53.syx"
```

then `tools/introboot.py` from the new `boot400M.snap` and `tools/uisettle.py`
after it, as below. The previous ladder is kept in
`snapshots/Digitakt_OS1.53.pre-mount-fix/`.

**What this means for dropping files on the card.** The firmware reads the
filesystem once, at mount, and caches the inode and block bitmaps in RAM.
Files written to `plusdrive.img` afterwards -- by `plusdrive-drop.cmd` or
`tools/ekfsadd.py` -- are on the card but not in that cached picture, so they
will not appear until the ladder is cold-rebuilt against the new image.

**Verified.** On the rebuilt ladder: `boot60M.snap` reports card `004GE0`,
manufacturer 0x11, storage error 0; by `boot400M.snap` the mounted flag is 1
and the inode bitmap has 6 bits set. On the rebuilt settled snapshot the press
that used to hang -- 6, then 15, then 12 -- now reaches page 1544, SETTINGS
with SAMPLES highlighted, with the main loop running at ~32 hits per 5M
instructions where it had been flat zero. 1544 is the page the live GUI was
frozen on, so this is the same state, alive.

**The key names in `devices/digitakt.toml` are right; an earlier version of
this paragraph was not.** It read the screen from `[0x4020d8f8]` after the
run, which is one flush stale (see *Headless screens were one key late*
below), so each key was credited with the page the key before it drew.
Latched at the flush entry: 6 (GLOBAL) opens SETTINGS with PROJECT
highlighted (1534), 15 (DOWN) moves the highlight to SAMPLES (1544), and 12
(YES) opens the browser.

**The sample browser then opened on an empty list. Fixed: the directory
indexes were never written.** With the +Drive mounted, SETTINGS > SAMPLES
stopped freezing and showed a `/` path bar over nothing.

Measured, in order. No filesystem code ran when the browser opened -- that
needs a raw count, not a diff against idling on SETTINGS, which is the first
way this was measured and which filters out anything that also runs while
the SAMPLES row is highlighted. The page's directory node is root (inode 2),
and its child vector was empty although `FUN_400d0580`, the refresh, had
run: its bitmap gate and its second gate both passed, and then the entry
iterator `FUN_400cdce4` returned one entry and then end-of-directory. That
iterator never scans the entry block. It walks an index -- logical block
`0x20001` of the directory -- and on this card that index, and both its
siblings, were all zeros. The firmware-formatted reference card has them
populated: 4/4/4 records for root, 2/2/2 for an empty `/incoming`.

`tools/ekfsadd.py` wrote those three blocks as zeros on format and never
touched them on add. The firmware's own insert, `FUN_400cd04c`, gives all
three formats (hash / name / inode; see `docs/mk1/10-plusdrive.md`), and the
writer now builds them by replaying that insert. Checked three ways: a fresh
format is **byte-identical** to the firmware's on both inodes and all eight
directory blocks; the hash and the name compare match the firmware's own
routines run under Unicorn (20 names, 47 pairs); and `tests/test_ekfs_index.py`
pins all of it to firmware-measured values. The same insert showed inode
`+0x02` is a link count, which this tool had been bumping once per file.

`python tools/ekfsadd.py IMAGE --repair` fixes a card an earlier version
wrote. The live card was repaired in place -- 6 blocks and one inode -- and,
because the firmware reads the tree only at mount, the ladder was cold-rebuilt
against it. After that, root's refresh returns 2 children where it returned
0, and the browser lists `factory` and `incoming`; entering `incoming` lists
both samples. Inside the browser too, 12 moves the cursor down and 15 enters.

Why yesterday's "format matches the device byte for byte" was wrong: that
comparison did not cover the directory blocks. This doc's own section on the
second fork said "the index's own format has not been decoded" -- which was
the bug, sitting in plain sight.

**Loading a sample then said "SAMPLE MEMORY FULL". Fixed: the files were not
samples.** Memory was never full -- the loader's own pre-check reported
67,108,944 bytes free. The message is simply what the load-done callback
`FUN_400544f6` prints when the worker returns a count of zero; with a nonzero
count it prints "%d/%d SAMPLES LOADED".

Found by replaying the session, not by guessing keys. The GUI logs every
panel message it feeds (`[gui] input --feed INSTR:HEX`), so resuming the same
snapshot against a byte-identical card and feeding the same bytes at the same
instruction counts reproduces the session exactly -- the pre-check fired at
403.1M in both. Instrumenting the "Load Samples" job's worker `FUN_400571ae`:
the directory node, the pool and a free slot were all fine, and the loader
`FUN_400ec6d2` was handed an **invalid file reference** (`ff ff ff ff ...`) and
failed at its first step. `FUN_400d1680` builds that reference only for an
inode whose `+0x0c` has bit 0 set, and `tools/ekfsadd.py` left `+0x0c` at 0.

`+0x0c` is a content hash, set by the firmware's file finaliser `FUN_400d1a2a`:
lookup3 (the superblock's hashbig) over the whole file with seed
`0x654c654b` -- ASCII "eLeK" -- `| 1`, stored in the inode and in an on-card
table at data block 64 that the mount loads into a sorted hash -> inode index.
Projects find samples by it. And behind it, the loader parses the file as the
firmware's own sample format -- a 64-byte header, big-endian 16-bit mono PCM,
16 zero bytes -- which a WAV is not. Both from the firmware's sample writer
`FUN_400eb666`; details in `docs/mk1/10-plusdrive.md`. The hash was checked by
running the firmware's own streaming hash under Unicorn on 13 lengths across
the 12-byte and 16 KB boundaries.

`tools/ekfsadd.py` now converts WAVs (8/16/24/32-bit and float, any channel
count, averaged to mono) into that format, hashes every file it writes, and
records the parent in `+0x08`. The two samples on the live card were raw WAV,
so its sample region was re-formatted and both re-added from their sources;
projects live outside that region and were not touched. Replaying the same
session against the rebuilt ladder: reference valid, file opens, 478,892
bytes read -- exactly 64 + 239,406 frames x 2 + 16 -- registered with the
engine, **"1 SAMPLE LOADED"**. `tests/test_ekfs_sample.py` pins it.

**Headless screens were one key late; NO was on the wrong code.**
`FUN_400e60e2` (`panel_diff`) sends the frame at `[0x4020d8f8]` against the one
at `[0x4020d8fc]`, then swaps the two pointers. At its entry `[0x4020d8f8]` is
the new frame, and that is where the GUI latches, so the GUI was always right.
Read at any other moment it is the frame before, because the flush only runs
when something changed. Every headless script that called
`panel.read(m, prof.fb_front)` after a spin therefore showed the page from one
key earlier -- the source of the "6 draws nothing" and "timing-dependent keys"
notes this section used to carry. Latch at the diff entry
(`at(prof.panel_diff, ...)`, as `panel.Capture` and `tools/emucheck.py` do), or
read `[0x4020d8fc]`.

With frames latched properly, a sweep of all 48 codes from SETTINGS and from
`/incoming` found NO: **code 13** backs out (SETTINGS -> main page; the browser
-> SETTINGS with SAMPLES highlighted), and FUNC+13 shows "Prj must be
re-saved!". The GUI's NO button had been sending code 0, which is not a key:
the firmware's panel-test names at `0x4018E278` list 16 trigs and 23 function
keys, codes 1-23 are those 23, and 0 did nothing from the main page, SETTINGS
or the browser. 13 had been drawn as a separate PATTERN MENU key (the
panel-test table's PATTERN MENU is the key the GUI calls SONG). Fixed in
`devices/digitakt.toml` and `emu/dtpanel.py`. In the browser, LEFT opens the
side menu (VIEW RAM / UPLOAD HERE) and YES on `..` goes up a folder.

**Key LEDs: decoded and drawn.** Everything the ColdFire tells the panel MCU
goes out on UART8 through one TX ring and lands in `ev['uart_out']`: OLED
tiles (`1p cc` + 8 bytes), an end-of-frame `B8` every frame, and the LEDs.
Each LED has four colour slots (`Bs id vv`: slot s := palette index vv),
`2g ss` picks one slot for each of LEDs 4g..4g+3, and `B5 i r g b` defines
the 41-entry palette (0..31 per channel; LED INTENSITY swaps palette tables,
so dim and bright are separate entries). `B7` is OLED contrast, not an LED.
The format was read from both ends -- the ColdFire builders (`0x400E6420`,
`0x400E66F4` slot cache, `0x400E62D8` selector flush, `0x400E65D6` palette)
and the panel MCU's own parser, whose firmware is embedded in the MAIN OS at
`0x4024E94C` -- and checked against the firmware's LED shadow in RAM at 93 of
93 checkpoints, plus a cold boot decoded with no byte left over.

`emu/panelleds.py` decodes it; `emu/gui.py` consumes the stream between
chunks (which also stops `uart_out` growing for the life of a session) and
seeds from the RAM shadow on resume, since a resumed stream has no history;
`emu/dtpanel.py` tints each key with its LED and draws the four
pattern-page LEDs under PAGE. The LED -> key map is `[panel.leds]` in the
device file. Blinking is the ColdFire resending selectors, so nothing blinks
on the host side. Checked through the GUI's own emulator thread: resume shows
SRC red, STOP white, trig 1 half red (selected track), page 1 dim; GLOBAL
lights GLOBAL; NO restores SRC; PLAY lights PLAY green. The decoder's own
finding matches the key sweep above: the firmware's UI-test table calls code
13 NO and has no code 0. LED 39 is an orange LED of unknown purpose.

**Audio: the loaded sample plays. The silence was the EMAC, not a missing
output.** The GUI had no audio model at all, but wiring one up (SSI1/eDMA 54
plus the software eDMA the render waits on) still captured pure zeros: the
patched Unicorn got the EMAC's arithmetic wrong in both modes the audio engine
uses. It computes at MACSR `0xA0` (signed fractional) -- where Unicorn
multiplied unsigned and dropped the product's `<< 1` (0.970 x 0.5 gave
0.2425), so the parameter smoother settled at 0.029 x its target, SAMP 1
selected slot 0, and every gain collapsed toward zero -- and mixes at `0x80`
(signed integer, saturating), where Unicorn had MACSR[S/U] backwards and ran
it unsigned, so every negative mix saturated to 0. Fixing only the first gave
a half-wave-rectified sample (correlation 0.71, about 1/sqrt 2, with the
source); fixing both gives the sample: correlation **-0.992** with slot 1's PCM
resampled to 48 kHz, both polarities, on WSL and on Windows through the GUI's
own emulator thread. (The sign is an inversion somewhere in the output path;
whether the hardware does the same is not known.) The fix is
`patches/unicorn-2.1.4-m68k-emac-modes.patch`, checked against the MCF5441x RM
(Table 5-3, the 5.3.5 pseudo-code); `emu/unicorn_compat.py` now refuses a
Unicorn without it and `tests/test_unicorn_emac.py` pins both modes.

**Audio plays live, at 48 kHz, in real time (09-23).** The GUI runs the
audio clock at the real rate and streams the render to the sound card as it
is produced (`[audio]` in `devices/digitakt.toml`: `request_hz = 48000`,
`ips = 64000000` after the intro). Measured headless through the GUI's own
emulator thread, with a stub output device standing in for the card, from
the loaded-sample snapshot: 100% of real time for 10 s with the sample
triggered, **no dropouts**, 60-80 ms buffered, and the recording correlates
-0.994 with slot 1 (the same inversion as above). The capacity behind that is
1.4-1.6x real time (2 emulated seconds in 1.26-1.42 s on WSL), so the pacing
sleeps rather than falls behind. The header has MUTE / PLAY / CLEAR /
SAVE WAV and a status line (`AUDIO LIVE · N ms buffered · N dropouts`);
`--no-audio` skips the model.

This needs the fast-memory and accelerator patches (`patches/README.md`).
Without them the same run is about nine times slower (11.3 s for those 2 emulated seconds), and the
GUI falls back to the old mode: audio clock at `fallback_request_hz = 2000`,
the render recorded and played back at 48 kHz afterwards, correct pitch,
heard after the fact.

What it took, in order of what it bought:

- **Memory stores on the fast path** (`unicorn-2.1.4-m68k-fast-mem.patch`).
  Stock Unicorn sends every load and store through a C helper as soon as any
  memory hook exists, and our demand mapper is one; its dirty bitmap is a
  stub, so every store also ran the self-modifying-code check. Routing hooks
  per page instead took the benchmark from 75% to 175% of real time.
- **Native helpers** (`unicorn-2.1.4-m68k-digikit-accel.patch`): the block
  budget of the fast stepper, the software-started eDMA the render starts and
  polls (`emu/edma_sw.py` is the Python original), and ISA_C for the CFV4E so
  FF1 (about 40 per render pass) is an instruction, not a hook.
- **Batched SSI** (`Ssi0Dma(batch=True)`): transmit and receive move a
  half-buffer (32 frames) at a time instead of stepping every frame. A
  vector the IPL blocks is retried a frame later, not a batch later; a batch
  later sent one half twice.
- **Fast idle**: the idle loop is skipped and credited to the budget, raising
  the reschedule vector when the credit crosses a yield point (crediting
  without that raised about a million vector 32s).
- **An emulated CPU clock of 64M instructions/s** after the intro. Counted
  instructions are the fast stepper's estimate, 3.87 per block; a render pass
  is about 26k of them (83k real instructions, 6,730 blocks) and 48 kHz needs
  1,500 passes a second.
- **Refused timer ticks wait** (`emu/pit.py`, `emu/dtim.py`). The render runs
  at raised IPL for most of each pass, and the timer models used to drop a
  tick the IPL refused. At 48 kHz that lost most of DTIM3's 30 Hz main-loop
  ticks, and the UI ran 4 passes a second instead of 30. A refused tick is
  now held, as PIF/REF hold it, and taken when the mask drops: 34 passes a
  second at 48 kHz, 30 at 2 kHz (was 17), and PIT0/PIT2/DTIM3 at their full
  50/60/30 Hz. `tests/test_timer_pending.py` pins it.

Two corrections came out of this. `emu/edma_sw.py` ignored SMOD/DMOD; it now
wraps modulo, as the ESG chains (channels 30 and 42) need. And the
software-eDMA segfaults have a measured cause, probably also behind the old
8-of-8 blamed on garbage DMA parameters (not re-run): AddressSanitizer showed
`uc_mem_map` called from inside a memory write hook (the software eDMA
mapping a page it was about to touch), which resizes the TLB under the store
in progress. Nothing maps from a hook now; a transfer that needs a page is
deferred to the next step boundary (the rules are in the `emu/edma_sw.py`
docstring). The fast-memory patch likewise defers a TLB refill requested from
inside a hook.

**More headroom: the speed patch (09-23).** On a slow laptop live audio ran
at 1.05-1.12x real time, too close to the edge. A sampling profiler inside
the library (host PC, pattern playing) put 12% of the time in an
exit-request check Unicorn compiles after every guest load and store, about
a quarter in the three helper calls each EMAC MAC compiled to, and a few
percent in re-translating a block for the exact PC at every hooked access;
Python was about 30%. `unicorn-2.1.4-m68k-digikit-speed.patch` fuses each
MAC into one helper and adds three engine options (`emu/native.py`): `rte`
done natively, no per-access exit check (faulting stores still stop at the
faulting instruction), and no PC rebuild for memory hooks (GUI only).
Measured in alternating runs, pattern playing:

| | five patches | six patches |
|---|---|---|
| WSL, deterministic replay, 4 emulated s | 1.72-1.97x | 2.69-2.83x |
| Windows, `tools/capbench.py`, 10 s | 1.88-1.92x | 2.53-2.59x |

The replay's audio and CPU state are byte-identical between the two
libraries, and `tools/livecheck.py --pattern` passes (15 of 15 beats, no
dropouts). The laptop was not re-measured; the Windows ratio (1.35x) puts it
near 1.45x. `tools/capbench.py` is the capacity bench: the GUI's emulator
thread with a stub sound card, the livecheck pattern programmed at real-time
pace, then measured with pacing off. (Its first version pressed the keys
unpaced, where a 0.15 s press lasts about 0.4 emulated seconds and DOWN
auto-repeats onto an empty slot, so YES opened the file browser; it measured
the snapshot's own pattern with the browser open. The ratio came out the
same.)

What is left, per the same profiler: the fused MAC helper about 20%,
translated code about 20%, Python about 30% (SSI batching and timer register
reads the largest parts).

**More tracks cost almost nothing (09-23).** `tools/capbench.py --tracks 8`
puts the sample on all eight tracks (TRK + trig selects one; `--screens`
saved every step, and all eight read SAMP 1) and grid-records each. Windows,
six-patch library, alternating runs: 2.46-2.56x with eight tracks playing
against 2.68-2.69x with one, about 1% a track; in the WSL deterministic
replay the gap is 0-6%, inside run-to-run noise. The reason is in a guest
profile (block counts over half an emulated second of each replay): the
firmware executes the same code, function for function within 0.5%, with
one track or eight -- 169-170M instructions per emulated second, a fifth of
them in `0x40072844` -- so it renders every voice every pass whether it
plays or not. Python's calls are the same count for count, and the host
profile has the same shape. The panel window itself costs about 3% (Tk under
Xvfb in WSL, 2.56-2.64x against 2.66-2.73x headless).

So splitting the voices across host threads, which would mean running the
firmware's voice render natively, has little to win, and so does moving the
emulator out of the GUI process. What bounds live audio is the fixed cost of
the render pass, on one emulated CPU. The levers are per instruction: the MAC
helper, the translated code, and the Python between steps (SSI and timers in
native code). On the laptop the old headroom (5-12%) was small enough that a
few percent more tipped it under real time; with the speed patch it should
have about 35% left with all eight tracks playing (not measured there yet).

**Patterns play (09-23).** The sequencer is clocked by the render through
two software-forced interrupts that nothing modelled, so until now PLAY set
the transport flags and no pattern ever advanced:

- The render counts samples down in `[0x80001f54]` (two per sample, from
  `[0x4020db60]` per pass). At zero it keeps the remainder in `0x80001f50`,
  parks the count at `0x7fffffff` and sets INTFRCH0 bit 12 (`0x40078406`):
  INTC0 source 44, which the RM lists as "Not used", ICR level 5, vector 108,
  handler `0x4006e756`. That handler acks the bit, runs the tick (and the MIDI
  clock, `pea $f8`), and sets INTFRCH0 bit 25 (`0x4006eae4`): source 57,
  level 2, vector 121, handler `0x4007041c`, which reloads the countdown
  (`0x4007145c`/`0x4007146e`). Undelivered, the render forced one tick and
  then waited on a parked countdown for ever.
- `emu/intfrc.py` delivers INTC0's forced sources (any with a non-zero ICR
  level, highest level first, taken when the IPL allows). The GUI installs it
  with the audio model; `tests/test_intfrc.py` pins it.

Measured at 48 kHz / 64M: vector 108 runs 157 times a second, which is
96 PPQN at the project's 98 BPM; `0x4199dc31` counts the 24 ticks of a step
and `0x4199dc30` the step. Through the GUI's own thread, headless with a stub
output device: STOP, grid-record trigs on steps 1/5/9/13 of the loaded
sample's track, PLAY -- the running light moves, TEMPO blinks on the beat,
and the recording has all 11 hits in 6.7 s on a 0.6122 s grid (a quarter note
at 98 BPM) at equal level, at 100% of real time with no dropouts. With the
sequencer running, capacity is still 1.4x real time on WSL.

`loaded.snap`-style snapshots restore with the transport already on, and PLAY
while playing pauses, as on the hardware: press STOP first.

Upstream digikit (checked 09-23) has no audio output at all: its audio lives
on the Digitakt II's SHARC, which nothing there emulates.





## Known limits

**Corrected 09-23: the sequencer notes below are superseded, and kept as the
record of what was seen.** `0x4199e70c` is not driven by the transport. It is
the render's write index, mod 32, into a 32-record message ring at
`0x4199e48c`: the render posts a type-`0x1b` record there and sends it with
`jsr 0x40001b7a` when voice state changes (`0x40077dbe`-`0x40077e0c`), so it
moves when voices start and stop and says nothing about the clock. "Nine
counts, then stalls" was the sequencer never being ticked at all: its tick is
a software-forced interrupt the render raises, which the emulator did not
deliver (see "Patterns play" under Audio). The bytes `0x4199dc30` and
`0x4199dc31` are the step and the tick within it, as `tools/seqtest.py` says;
they did not move because no tick ran. The starvation threshold and the
"every number is from a 2 kHz run" trap still describe the old 4.68M clock.

**The sequencer runs. What does not move is a flag.** This file has said
for a long time that PLAY arms the transport but the playhead never advances,
and that the per-tick position commit is "audio-side". Both halves are wrong,
and both were wrong about which words to read.

Measured by differencing 64 KB of sequencer RAM with PLAY pressed against an
idle control, then testing every survivor against STOP:

| word | idle | playing | after STOP | what it is |
|---|---|---|---|---|
| `0x4199e70c` | 0 | +9, then stalls | holds, not reset | the only word the transport drives |
| `0x4199dbb4` | 0 | 65536 | 0 | a flag PLAY sets, not a position |
| `0x4199dc2c` | 0 | 1 | 0 | a second transport flag |
| `0x4199dc30` | 0 | 1 | **1** | latches at PLAY and stays after STOP |
| `0x4199dd08` | 0 | 0 | 0 | not involved |

`0x4199dbb4` and `0x4199dd08` are the pair this file used to call the playhead
and its source. `0x4199dc2c` and `0x4199dc30` are the Digitakt II pair that
`tools/seqtest.py` and `tools/unblock.py` still carry. All four are flags or
latches. The rule `0x4199dbb4 := [0x4199dd08] + 1` was never the live one:
65536 is not 0 + 1, and 65536 is `0x10000`.

**Each PLAY yields exactly nine counts, then stalls.** With SSI1 at 2 kHz
`0x4199e70c` takes one count the instant PLAY is pressed and eight more over
the next 130M instructions, then holds with the transport still on. STOP does
not reset it: every flag above returns to 0 and this one keeps its value.

Pressing PLAY a second time resumes it rather than restarting it, and gives
the same nine:

| | at the press | over the next 140M | then |
|---|---|---|---|
| first PLAY | 1 | 2 3 5 6 7 8 9 | holds at 9 |
| second PLAY | 10 | 11 12 14 15 16 17 18 | run ended here |

So 9 is not a ceiling on the counter; it is how far one transport start gets
before something stops it. **Eight is also the number of audio tracks on a
Digitakt mk1**, which is worth chasing and is not evidence. An empty +Drive,
with no sample for a voice to play, is the other obvious suspect. Neither has
been tested.

**The sequencer's time base is SSI1.** The same test at three audio rates:

| SSI1 rate | instructions per increment | rate x interval |
|---|---|---|
| 1 kHz | 33.3M | 33.3 |
| 2 kHz | 16.25M | 32.5 |
| 3 kHz | 11.25M | 33.75 |

The product is constant within 4%, so `0x4199e70c` is clocked by the audio
sample rate, not by a timer and not by instruction count. It stalls after the
same nine counts at 2 kHz and at 3 kHz; the 1 kHz run ended at 7 having had
time for only 6, which is consistent with the same nine rather than evidence
against it.

Scaling to a real 48 kHz gives about 690,000 instructions per increment, near
7 per second at this emulator's instruction rate. Sixteenth notes at 120 BPM
would be 8 per second. Close enough to be worth chasing, not close enough to
assert: the instructions-per-second constant is itself approximate.

**The starvation threshold is between 3 and 4 kHz.** PLAY arms at 1, 2 and
3 kHz and does not at 4 kHz or above, where the run still boots and still
prints a full table of zeroes that reads like a result. Any test that presses
keys with audio armed must check that the key registered before believing the
table.

Two traps this cost, both worth more than the result:

- A user-interface test cannot be run at the real sample rate. At 48 kHz an
  audio interrupt arrives every ~3,120 emulated instructions and the render
  needs ~18,500, so the UI never gets to run and PLAY is never even seen. The
  transport word stays 0 for the whole test, which reads exactly like "PLAY
  does nothing". Every number here is from a 2 kHz run. (That was at 4.68M
  instructions a second. At the 64M the live-audio GUI now uses, 48 kHz
  leaves the UI its full rate; the sequencer tests above have not been
  repeated there.)
- A "strictly rising on every sample" filter cannot see a counter that wraps.
  `0x4199e3c8` passed that filter with PLAY pressed and failed it idle, purely
  because of where its 32-bit wrap fell, and it looked like a second transport
  counter. It keeps rising after STOP. Only the STOP test told them apart.

**RETRACTED, and see `docs/mk1/11-audio.md`.** This section used to say audio
was not on the ColdFire at all, that section 8 was the audio coprocessor, and
that emulating it would unlock both the sequencer and sound. All three are
wrong.

SSI1 is at `0xFC0C8000` on this part; the scan that found "zero references to
SSI0/SSI1" was looking at the wrong addresses, and `docs/mk1/03-peripherals.md`
had the right one in its own table the whole time. Section 8 is the USB/MIDI
interface controller -- its string table names `USB dev task`, `MIDI task`,
`Invalid vid/pid` and FreeRTOS's `Tmr Svc` -- not the sample engine.

The audio engine is ColdFire code: `FUN_40077420` on vector 191, rendering
into a 512-byte double buffer at `0x4ba8f080` that eDMA channel 54 drains into
SSI1, computing with the EMAC accumulators. The emulator now drives that chain
and delivers its interrupts; the render ISR runs three passes and stops on a
DMA status bit, which `docs/mk1/11-audio.md` locates exactly.
