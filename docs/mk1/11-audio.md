# 11 — Audio, and where it really runs

Digitakt mk1, OS 1.53. This corrects the largest standing claim in these
notes. Everything here was measured on this branch: addresses come from
Ghidra over the mk1 image, counts from running it.

## The claim this replaces

`DIGITAKT-MK1.md` said, under "Known limits":

> Audio is not on the ColdFire at all — there are zero references to SSI0/SSI1
> anywhere in MAIN OS. Sound goes to a coprocessor over DSPI, and on mk1 that
> blob (section 8 of the `.syx`, ~160 KB) is **ARM Thumb-2** [...] The UI
> blocker and the audio blocker are one blocker: emulating that coprocessor
> would unlock both.

Three parts of that are wrong, and the third is the expensive one.

**SSI is referenced.** SSI1 is at `0xFC0C8000` on this part, not the address
the earlier scan used. `03-peripherals.md` had it right in its own table all
along; the "zero references" sentence was checking somewhere else.

**Section 8 is not the audio coprocessor.** Its string table, which an earlier
scan reported as absent because the Thumb code around it produces enough
printable junk to bury it, is unambiguous once pulled out as NUL-terminated
runs:

    USB PD Task | USB task | USB dev task | MIDI task | Main task | GPI task
    Audio task | Release mute | USB Host delay | Tmr Svc | Computer/Host
    Invalid vid/pid | Missing config index | Flash OK | Calib OK
    {"version": "1.00E", "release": "0018"}

`Tmr Svc` is FreeRTOS's timer-service task. `vid/pid`, `USB dev task` and
`Computer/Host` are USB. This is the USB/MIDI interface controller — the
Overbridge side — running its own FreeRTOS on ARM, with its own firmware
version and its own upgrade protocol. It is not the sample engine.

It also has a header, at file offset `0x1000` after 4 KB of `0xFF` padding,
whose little-endian words point into `0x6004xxxx`: `+0x04` is `0x60042000`,
which is exactly where content resumes after the second fill run. Base
`0x60040000` with SRAM at `0x20000000` is an ordinary Cortex-M map.

**And so the two blockers are not one blocker.** The audio engine is ColdFire
code, on the processor this project already emulates.

## What the audio path actually is

`FUN_40000f64` sets it up, and it is all on-chip:

| piece | address |
|---|---|
| SSI1 | `0xFC0C8000`; STX0 at +0x00, SRX0 at +0x08, SCR +0x10, STCCR +0x20 |
| transmit buffer | `0x4ba8f080`, 512 bytes, cleared at init |
| transmit DMA | eDMA channel 54, TCD at `0xFC0456C0` |
| receive DMA | eDMA channel 52, TCD at `0xFC045680`, into `0x80001000` |
| transmit complete | INTC1 source 46 -> vector 174, handler `0x40076C38` |
| render | INTC1 source 63 (software) -> vector 191, `0x40077420` |

The transmit descriptor reads: `SOFF 4`, `NBYTES 8`, `CITER = BITER = 64`,
`DOFF 0`, `SLAST -512`, `CSR 6`. So one minor loop is 8 bytes -- a stereo pair
of 32-bit words -- sixty-four of them fill the 512-byte buffer, `SLAST` rewinds
it in place, and `CSR 6` is `INT_HALF | INT_MAJOR`: a double buffer, not
scatter-gather. 64 x 8 = 512 exactly.

The chain:

    SSI1 frame request
      -> eDMA ch54 minor loop, 8 bytes out of the buffer
      -> at the half and at the end, vector 174
      -> that ISR counts, timestamps from DTIM2, sets a refill flag, clears
         the DMA interrupt, and sets INTC1 INTFRCH bit 31
      -> vector 191, the render routine

Vector 174's handler is five statements long. All the work is in vector 191.

## Vector 191 is a software synthesiser

`FUN_40077420` decompiles to exactly what a double-buffered renderer looks
like. It clears its own force bit, reads the LIVE DMA pointers -- `0xFC0456C0`
(ch54 SADDR) compared against `0x4ba8f180`, the buffer's midpoint, and
`0xFC045690` (ch52 DADDR) for input -- to decide which half is safe to write,
and then computes with the ColdFire's EMAC unit: `emacSaturate` over `ACC0`
through `ACC3`, `mac.l` and `movclr.l` throughout the code it calls.

That is why this repo has a `ColdfireEMAC` Ghidra language at all. The
accumulators are not incidental; they are the synthesis.

So: **the sample engine runs on the ColdFire, in an interrupt handler, at IPL
5, feeding a codec over SSI1 by DMA.** No second processor participates in
making sound.

## What the emulator now does, and what it does not

`emu/ssi.py` already modelled this chain for Digitakt II, and is now
parameterised by a `Profile` instead of copied. `emu/edma_sw.py` is new, for
the block moves the render hands to eDMA and then polls.

Driving the chain at an explicitly assumed 48 kHz (the board's SSI clock is
not recovered, so the rate is stated, never inferred), over 60M instructions:

| | |
|---|---|
| audio interrupts delivered | 26,346 |
| half / full | alternating exactly as the descriptor asks |
| render ISR passes | 4,014 |
| software DMA channels running | 30, 31, 32, 42 |
| DMA transfers | 58,337, none refused |
| writes into the transmit buffer | 256,768 |
| value of every one of them | zero |

**The audio engine runs.** It is entered by its own interrupt chain, completes,
and is entered again, thousands of times, and it writes its output buffer:
exactly 64 words per pass, which is one half of the 128-word buffer, which is
precisely what a double-buffered renderer must do. The two instructions doing
almost all of it, `0x40072214` and `0x4007221a`, are a pair -- left and right.

What it renders is silence. That is the correct output for a machine with an
empty +Drive and nothing playing, and it is not the same claim as "it does
nothing": a run that never touched the buffer and a run that fills it with
zeros look identical in the captured PCM, which is why the writes are counted
separately. Hearing anything needs a sample, and that needs the +Drive writer
in `10-plusdrive.md`.

### Four things were in the way

Every one was in this model rather than the firmware, and each was found by
measuring rather than by reasoning about what a bit "should" mean.

1. **Channel 32's DONE never set.** Nothing drove software-started eDMA
   channels at all. `emu/edma_sw.py`.
2. **DONE was written inside the guest's own write** to that register, so the
   guest's pending value landed afterwards and erased it. The transfer ran and
   the poll never ended, which is indistinguishable from the transfer never
   running. It is queued and applied from the run loop, the line
   `emu/edma.py` already draws for completion interrupts.
3. **The completion value re-set E_SG.** Channel 30 starts with CSR `0x0011`
   -- START plus E_SG -- and its caller spins until bit 4 clears. The
   scatter-gather chain was being followed correctly, but the completion was
   computed from the CSR the *guest* wrote, giving `0x0090`, bit 4 still set.
   Hardware ends up with the last loaded descriptor's CSR, which is where
   E_SG goes away. Renders went 3 to 5.
4. **Unmapped SDRAM.** Running further reached a descriptor whose source the
   guest had not written, so it was not demand-mapped and the read faulted.
   Hardware has no such notion. Both ends are now mapped first, as
   `emu/esdhc.py` already does. Renders went 5 to 3,241.

And one mistake worth keeping on its own: `CITER`/`BITER` have a linked form,
and with bit 15 set the count is only bits 8:0. Reading the wide field turned
channel 32's nine-iteration move into 16,393 of them, writing 262 KB through
unrelated memory -- after which the render got *further*, which is exactly
what memory corruption can look like from the outside.



## The input path, proved separately

"Renders silence" and "writes zeros unconditionally" cannot be told apart by
looking at the output, so the input answers it instead: it needs no sample, no
sequencer and no menu.

`emu/ssi.py` moves the receive channel's descriptor but deliberately never
wrote its destination -- there was no peer to receive from, and inventing data
would have been worse than leaving the buffer alone. `rx_source` is the
missing half, and with it a 1 kHz sine pushed into the SSI receive FIFO:

| | |
|---|---|
| fed in | 3,076,940 bytes |
| receive buffer at `0x80001000` | **128 of 128 words non-zero** |
| first words | `2614b88b 2614b88b 21f0ec20 21f0ec20 1d387437 1d387437` |
| transmit buffer | still zero |

Those words are the sine, in pairs, one per channel. Later in the run the
buffer's range grows past the amplitude that was fed in, so **the firmware is
not merely receiving it, it is processing it**.

Nothing of it reaches the output, and that is expected: input monitoring is a
setting, and nothing here has turned it on. It is not a property of the engine.

So both directions of the audio path now run: a signal put in is captured and
worked on, and the renderer is entered, computes, and fills its buffer. What
is missing for an audible result is something to play.


## The sequencer, re-tested against a running engine

> **Corrected 2026-09-23; this section is kept as the record of what was
> seen.** `0x4199e70c` is not a transport counter: it is the render's write
> index (mod 32) into a message ring at `0x4199e48c`, advanced when voice
> state changes. The sequencer was never ticked at all -- its tick is a
> software-forced interrupt the render raises (INTC0 source 44, vector 108,
> then source 57, vector 121), which the emulator did not deliver.
> `emu/intfrc.py` delivers it now, and patterns play at tempo; see
> `DIGITAKT-MK1.md` ("Patterns play").

The old note blamed the playhead on the coprocessor that turned out to be a
USB controller, so it was re-measured rather than inherited. The re-measure
found something the old framing could not: **the sequencer does advance.**
What never moves is a flag.

Rather than trusting any inherited address, 64 KB of sequencer RAM was
sampled with PLAY pressed and again idle, and every word that rose in both
arms was discarded as a free-running clock. The survivors were then tested
against STOP:

| word | idle | playing | after STOP | what it is |
|---|---|---|---|---|
| `0x4199e70c` | 0 | +9, then stalls | holds, not reset | the only word the transport drives |
| `0x4199dbb4` | 0 | 65536 | 0 | a flag PLAY sets, not a position |
| `0x4199dc2c` | 0 | 1 | 0 | a second transport flag |
| `0x4199dc30` | 0 | 1 | **1** | latches at PLAY and stays after STOP |
| `0x4199dd08` | 0 | 0 | 0 | not involved |

So audio was never what held the sequencer, and neither was anything else.
`0x4199e70c` advances while the transport is on and freezes the moment it
goes off. Every address this project has called the playhead turns out to be
a flag that PLAY sets, or in the case of `0x4199dc30` a latch that PLAY sets
and STOP leaves alone.

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

Two traps, both of which produced a confident wrong answer first:

- A user-interface test cannot be run at the real sample rate. At 48 kHz an
  audio interrupt arrives every ~3,120 emulated instructions and the render
  needs ~18,500, so the UI never gets to run and PLAY is never even seen. The
  transport word stays 0 for the whole test, which reads exactly like "PLAY
  does nothing". Every number here is from a 2 kHz run.
- A "strictly rising on every sample" filter cannot see a counter that wraps.
  `0x4199e3c8` passed that filter with PLAY pressed and failed it idle, purely
  because of where its 32-bit wrap fell, and it looked like a second transport
  counter. It keeps rising after STOP. Only the STOP test told them apart.

## Still open

- **A sound.** The renderer runs and writes silence. Producing anything
  audible needs a sample on the drive, which needs the +Drive writer.
- The SSI clock. `STCCR` is `0x480`, or `0x488` when bit 19 of `0x401f55c0` is
  set -- two rates, presumably 44.1 and 48 kHz, but the input clock is not
  known so neither is the output rate.
- The receive path. Channel 52 captures into `0x80001000` and nothing has
  exercised it.
