# The +Drive on the card

Digitakt mk1, OS 1.53. Everything here was measured by running the firmware's
own `SETTINGS > SYSTEM > FORMAT +DRIVE` under emulation with both boxes ticked
(PROJECTS+SOUNDS and SAMPLES) and recording every eSDHC command in issue
order. Nothing is inferred from the eMMC spec beyond what the command numbers
mean.

The capture is reproducible: the menu path is GLOBAL, DOWN x8, YES (SYSTEM),
DOWN x5, YES (FORMAT +DRIVE), then `Y D Y D Y Y` inside the dialog -- YES ticks
PROJECTS+SOUNDS, DOWN/YES ticks SAMPLES, DOWN/YES lands on ERASE SELECTED
DATA, and the last YES confirms. Card traffic starts on that last YES.

## What the format issues

| command | count | meaning |
|---|---|---|
| 35 / 36 / 38 | 515 each | ERASE_GROUP_START / ERASE_GROUP_END / ERASE |
| 18 | 6,588 | READ_MULTIPLE_BLOCK |
| 23 / 25 | 161 each | SET_BLOCK_COUNT / WRITE_MULTIPLE_BLOCK |

A format erases the three regions and then writes 161 blocks, 96 of them into
the sample region, laying down a real filesystem. See "ekFS" below.

**An earlier version of this file said a format was "an erase sweep plus two
512-byte writes" that left no structure behind. That was wrong, and the way it
was wrong is worth keeping.** It was measured honestly from a command log --
the log really did contain two writes. The cause was in the emulator: the card
model packed EXT_CSD's SEC_COUNT little-endian, and this firmware reads that
field as a big-endian longword, so 0x00760000 arrived as 0x00007600 and the
driver believed the card held 30,208 sectors, 14.75 MB. The write primitive at
`0x400e2bd2` range-checks `sector >= capacity` and returns -10 **before
issuing any command**, so every write above 15 MB vanished with no card
traffic to notice. Erase takes a different path with no range check, which is
why the erase sweep went through and made the whole thing look like deliberate
firmware behaviour rather than a dropped write.

The lesson for anything else measured this way: a command log shows what was
issued, never what was refused on the way. A silent-drop bug reads exactly
like a design decision.

## The map

The 515 erase ranges merge into exactly three contiguous regions. All
addresses are sector numbers (512 bytes) and byte offsets from the start of
the card.

| region | sectors | bytes | size | what |
|---|---|---|---|---|
| superblock | 0 | `0x00000000` | 512 B | magic `be ef ba ce`, rest zero |
| (unnamed) | 2048 | `0x00100000` | 512 B | written at boot and again by format |
| +Drive sounds | 4096 – 8191 | `0x00200000` – `0x00400000` | 2 MB | 2048 slots of 1 KB |
| projects | 524288 – 1572863 | `0x10000000` – `0x30000000` | 512 MB | |
| samples | 1835008 – 3816527 | `0x38000000` – `0x7478a000` | 967.54 MB | |

Two gaps are deliberately not erased: 4 MB to 256 MB, and 768 MB to 896 MB.
What lives there is not yet known.

The sample region's size is the strongest check on this map. Elektron
advertise the mk1's +Drive as 1 GB of sample memory, and the region the
firmware erases for SAMPLES is 967.54 MB -- 1 GB of the manufacturer's
decimal gigabytes is 953.7 MiB, so the two agree to within the slack you
would expect from rounding a region to erase-group boundaries.

## Sound slots are 1 KB each, and the reads prove it

After erasing, the format reads the 2 MB sound region back as 2,048 CMD18
reads starting at sector 4096 and stepping exactly 2 sectors each time. Two
sectors is 1,024 bytes, so:

    +Drive sound slot N is at byte offset 0x200000 + N * 1024

2,048 slots is the same 2,048 the boot job sorts: the "FACTORY PROJECT >>
+DRIVE" job walks a collection at `0x42a3d6b0` whose indices run 0..2047 and
whose names are all empty on a blank drive, which is why that sort is
quadratic and takes about a billion instructions to finish.

## What the emulator had to learn

`emu/esdhc.py` did not model erase at all. CMD35/36/38 fell through to the
generic "command accepted" response, so the firmware's format completed
happily and every byte it meant to erase stayed exactly where it was. With a
persisted image (`DT2_PLUSDRIVE`) that is a silent correctness bug: formatting
the drive did nothing.

Erase is now modelled as a list of byte ranges rather than expanded into the
per-byte overlay -- 1.5 GB of erase would otherwise be gigabytes of host
memory for data that is all one value. Reads apply erased ranges before the
overlay, so a write after an erase still wins, and `flush()` only zeroes the
part of a range the image file actually covers, because past end-of-file a
sparse image already reads as zero. Erased ranges travel through checkpoints;
a snapshot written before this existed simply has none, which is what it
recorded.

Erased eMMC reads back as a constant chosen by EXT_CSD's ERASED_MEM_CONT,
which this model leaves at 0, so the constant is `0x00` -- the same value an
untouched sparse image gives, so a restored snapshot cannot disagree with its
own backing file.

## Checked end to end

Unit tests cover the command sequence, but the claim worth checking is the
whole chain: does the *firmware's own* format now clear a real image? Fill a
4 MB card image with `0xEE`, boot, and drive the menu exactly as above. After
the format:

| region | before | after |
|---|---|---|
| sector 0, superblock | `0xEE` | `be ef ba ce` then zeros |
| `0x000200`–`0x100000`, not erased | `0xEE` | `0xEE`, untouched |
| sector 2048 record | `0xEE` | all zero, written |
| `0x100200`–`0x200000`, not erased | `0xEE` | `0xEE`, untouched |
| `0x200000`–`0x400000`, sound region | `0xEE` | all zero |

The untouched gaps are the useful half of that result. They say the erase is
bounded exactly where the command log says it is, rather than being a blanket
wipe that would have cleared the sound region by accident.

Once SEC_COUNT was fixed the same run also produces a formatted drive, and
that is the check on everything in the ekFS section below: the image grows to
905 MB, all 161 region-level writes reach the card as CMD25 (a 1:1 match,
where it had been 193 calls and 0 commands), and the superblock read back off
the image at sector `0x1c0000` carries `"ekFS"`, version 2, `0xefe0` usable
blocks, bitmap offset `0x30` and data offset `0x4050` -- every field as read
out of the mkfs code, now confirmed from the other end.

Snapshots taken before the fix store the wrong capacity in the driver's own
RAM, and `sd_bringup` runs long before the first ladder rung, so a resume
never re-derives it -- only a cold boot can. The ladder under
`snapshots/Digitakt_OS1.53/` has since been rebuilt from cold. **Six of its
seven files carry 7,733,248 sectors**; the seventh is `ui.snap`, carried over
from the old ladder because `tools/uisnap.py` no longer reaches a live UI
from either ladder. Run against the OLD `boot400M.snap` as a control it
reports `lit=367 mainloop=0` all the way to its 900M budget and refuses to
save; the rebuilt rung does the same to 2.5G. So this is not a regression in
the rebuild, it is an open question about `uisnap.py` -- and only
`tools/dtdrive.py` reads `ui.snap`. The previous ladder is kept at
`snapshots/Digitakt_OS1.53_pre_seccount_fix/`.

**Superseded 2026-09-22:** the card now reports **3,866,624** sectors (`0x3b0000`), not 7,733,248. That is the SLC figure the firmware's eMMC part table requires alongside the SLC flag; the full-capacity figure with the flag set fails identification and the +Drive is never mounted. See `DIGITAKT-MK1.md`. The ladder has been cold-rebuilt three times since, and the snapshots named above are archival.

`emu/esdhc.py` still reasserts the value on restore and prints when it has
to, which is now a safety net rather than something the rebuilt rungs need:

```
  open the pre-rebuild gui.snap:
  [esdhc] driver capacity 30208 -> 7733248 sectors (stale snapshot)
  open the rebuilt one:
  (nothing)
```


## The sample filesystem: "ekFS"

The sample region is not raw. It carries a filesystem with its own superblock,
free bitmap and fixed-size allocation blocks, and the firmware's mkfs for it is
`FUN_400d0cb8` (reached from `FUN_400d0f70`). Read out of that function and
its allocator, `FUN_400cc560` / `FUN_400cc61a`.

All offsets below are **sectors relative to the region base**, sector
`0x1c0000` (1835008), which is the same base the erase sweep uses.

| offset | size | what |
|---|---|---|
| `+0x0000` | 512 B | superblock |
| `+0x0030` | `0x4000` B (32 sectors) | free-block bitmap, 1 bit per block |
| `+0x4050` | rest | data area, blocks of `0x20` sectors |

    block N lives at sector 0x1c4050 + N * 0x20      (16 KB per block)

The allocator is explicit about both numbers. `FUN_400cc560(block, count)`
frees a run: it clears `count` bits in the bitmap, writes the bitmap back with
`write(0x1c0030, 0x4000, ...)`, and then erases `block * 0x20 + 0x1c4050` for
`count << 5` sectors -- so the unit is 32 sectors and the data area starts at
`0x1c4050`. `FUN_400cc61a` indexes the bitmap as `(block >> 17) + 0x1c0030`,
and `0x4000` bytes is exactly 2^17 bits, so one chunk covers the whole drive
at one bit per block.

### The superblock

512 bytes, built in RAM at `0x42685490` and written with
`write(0x1c0000, 0x200, ...)`.

| offset | value on a fresh format | meaning |
|---|---|---|
| `+0x00` | `0x656b4653` = `"ekFS"` | magic |
| `+0x04` | 2 | version |
| `+0x08` | `0x4000` | bitmap size in bytes |
| `+0x0c` | `0x10000` | blocks the bitmap is sized for (65536) |
| `+0x10` | `0xefe0` | usable blocks (61408) |
| `+0x14` | `0x10` | |
| `+0x18` | `0x30` | bitmap offset, in sectors |
| `+0x1c` | `0x50` | |
| `+0x20` | `0x4050` | data-area offset, in sectors |
| `+0x24` | `0x10` | |
| `+0x28` | `0x40` | |
| `+0x2c` | allocated | first index run (16 blocks) |
| `+0x30` | allocated | second index run (16 blocks) |
| `+0x1fc` | checksum | over the preceding 508 bytes, seed `0x31323334` |

The usable-block count is the arithmetic check that ties this back to the
erase map. The region is `0x1e3c50` sectors; take away the `0x4050` before the
data area and 1,965,056 sectors remain, which at 32 sectors per block is
**61,408 blocks — exactly the `0xefe0` the superblock records**, and 959.5 MiB
of sample data. Nothing here was fitted; the region length came from the erase
commands and the block size from the allocator, and they agree.

Immediately after mkfs writes the superblock it allocates 64 blocks and then
two runs of 16, and initialises them in place -- the first run lands at
`0x1c4850`, which is `0x1c4050 + 64 * 0x20`, confirming the block formula from
a second direction.

### Inodes, bitmaps and the rest of the region

The block allocator was only half of it. `FUN_400d2290` (allocate an inode) and
`FUN_400d207e` (write one back) give the rest, and the arithmetic closes
exactly against the data offset already known:

| offset (sectors from `0x1c0000`) | size | what |
|---|---|---|
| `+0x0000` | 512 B | superblock |
| `+0x0010` | `0x4000` B, 32 sectors | inode bitmap, one bit per inode |
| `+0x0030` | `0x4000` B, 32 sectors | block bitmap, one bit per block |
| `+0x0050` | 8 MB, `0x4000` sectors | inode table, 65536 x 128 B |
| `+0x4050` | rest | data blocks, 16 KB each |

`0x50 + 0x4000 = 0x4050`: the inode table runs exactly up to where the data
area starts, which is the check that these are the real boundaries and not a
plausible reading of them.

Writing inode N is a read-modify-write of one 16 KB chunk:

    sector = ((N << 7) >> 14) * 0x20 + 0x1c0050      i.e. (N / 128) * 32
    offset = (N & 0x7f) * 0x80                       128 inodes per chunk

so an inode is **128 bytes**, and valid numbers are 2..65535 (`N - 2 < 0xfffe`
guards every lookup). Inode +0x00 is a type byte, 1 for a directory;
`FUN_400d2290` sets +0x01 to 2 and puts a monotonic serial at +0x10.

**Inodes at or above `0x01000000` never touch the card.** `FUN_400d207e`
branches on exactly that, keeping them in RAM instead. That is not a
curiosity: it is what the `/factory` tree is, and the root directory on a
formatted image names `factory` with inode `0x01000000` -- the first
RAM-only number.

This also finishes the superblock. `+0x14` is the inode-bitmap offset and
`+0x1c` the inode-table offset, the two fields left as `?` above; `+0x0c`'s
`0x10000` is the inode count, matching the 65536 bits a `0x4000`-byte bitmap
holds.

### Inside a 128-byte inode

Read off `/incoming` (inode 3) on a formatted image and checked by following
the pointer, not by reading the field names off a spec:

| offset | value on `/incoming` | field |
|---|---|---|
| `+0x00` | 1 | type; 1 = directory |
| `+0x01` | 2 | set to 2 by the allocator |
| `+0x04` | `0x4000` | size in bytes |
| `+0x08` | 3 | the inode's own number |
| `+0x10` | 3 | serial, from a counter that only goes up |
| `+0x1c` | 2 | parent inode |
| `+0x24` | 1 | blocks in the first fork |
| `+0x28` | 100 | first block of the first fork |
| `+0x2c` | `0x20000` | second fork, size |
| `+0x30` | 3 | second fork, block count |
| `+0x34` | 101 | second fork, first block |

**`+0x28` really is the data block.** Block 100 holds `/incoming`'s own
directory content -- a `.` pointing at inode 3 and a `..` pointing at root's
inode 2 -- which is what makes this a measurement rather than a plausible
reading of the bytes.

The second fork is not a guess either. A directory's initialiser allocates
two extents, `0x4000` bytes at logical offset 0 and `0xc000` at offset
`0x80000000`; the inode records three blocks for the second, and 3 x 16 KB is
exactly that `0xc000`. Blocks 101-103 hold what looks like a name index --
block 102 contains the strings `.` and `..` beside 32-bit values that are not
block numbers -- so directory lookup is hashed rather than linear.

**Decoded since, and it mattered.** The second fork holds three sorted
indexes over the directory's entries, one block each, and the firmware lists
and searches directories **only** through them -- the iterator the UI lists
with, `FUN_400cdce4`, never scans the entry block. Each block is a `u16`
count at `+0`, then 8-byte records from `+8`: `u32 key, u32 location`, where
location = `logical_block << 14 | offset` of the entry. From the firmware's
own insert, `FUN_400cd04c`, and reproduced byte for byte against this
formatted image:

| logical | key | order |
|---|---|---|
| `0x20000` | `FUN_400cccb6(name)` -- ext3's legacy `dx_hack_hash` | ascending, unsigned |
| `0x20001` | first 4 bytes of the name | `.` and `..` first, then directories, then files, each by `FUN_400e8606` |
| `0x20002` | the entry's inode number | ascending |

Ties go after existing equal entries, and the insert refuses a 2001st entry.
`FUN_400e8606` is a natural-order, case-insensitive compare (digit runs by
value, then by leading-zero count; everything else through `toupper`) with a
quirk: after an equal digit run it skips the next character on both sides
unread, so `a1b` and `a1c` compare equal. Both the hash and the compare were
checked by running the firmware's own routines under Unicorn -- 20 names and
47 pairs, no mismatches.

The name keys for `.` and `..` are `0x2e00534f` and `0x2e2e0053`: the insert
reads four bytes from a pointer to the literal `"."`/`".."` and runs past its
NUL into the next string in rodata. Constant, so reproducible.

**Why this mattered.** `tools/ekfsadd.py` wrote these three blocks as zeros,
so on any card it formatted or added a file to, the firmware saw every
directory as empty -- SETTINGS > SAMPLES opened on a blank list. It builds
them now (by replaying the firmware's insertion loop, since a compare with
that quirk is not guaranteed transitive and a sort could disagree), and
`ekfsadd.py IMAGE --repair` rebuilds them on a card the old version wrote.

### Sample files

A sample on the +Drive is not a WAV. The firmware's sample writer
`FUN_400eb666` lays one down as:

| offset | field |
|---|---|
| `+0x00` | u32 0 |
| `+0x04` | u32 PCM length in bytes |
| `+0x08` | u32 sample rate (the recorder writes 48000; the engine takes 0 as 48000) |
| `+0x0c` | u32 0 |
| `+0x10` | u32 0 |
| `+0x14` | u8 `0x7f` |
| `+0x15`..`+0x3f` | 0 |
| `+0x40` | PCM: 16-bit mono, **big-endian** |
| end | 16 zero bytes (copied from a BSS buffer nothing writes: interpolator padding) |

The values are the ones its recorder caller `FUN_400eb922` passes. The loader
`FUN_400ec6d2` reads the whole file verbatim into sample RAM (base
`0x4bbaf5f0`, at most `0x4000050` bytes including header and trailer) and
hands `+0x04`, `+0x08` and the PCM to the engine, `FUN_400763b4`. The byte at
`+0x01` becomes a per-slot flag when it equals 1; the recorder writes 0.

**Every finished file carries a content hash.** `FUN_400d1a2a` runs lookup3 --
the superblock's hashbig -- over the whole file with seed `0x654c654b`
("eLeK"), streaming it in 16 KB chunks, and stores `hash | 1` in inode `+0x0c`
and in a table: data blocks 64..79, 4096 u32 words each, word `inode & 0xfff`
of block `64 + (inode >> 12)`. The mount loads every word with bit 0 set into
a sorted hash -> inode index (`FUN_400d1932`), alongside the RAM factory
inodes, and `FUN_400d1c90` falls back to it when a stored reference no longer
matches its inode -- which is how a project finds a moved sample. A file whose
`+0x0c` lacks bit 0 is not a sample to `FUN_400d1680`, and loading it fails.
Checked by running the firmware's own streaming hash under Unicorn over 13
lengths straddling the 12-byte and 16 KB boundaries, all equal.

### Directory entries

Read off a formatted image rather than inferred: the root directory sits in
data block 96 (byte `0x180000` into the data area, sector `0x1c4c50`) and
contains four entries.

    00 00 00 02  00 0c  01  01  2e 00 00 00              "."        -> inode 2
    00 00 00 02  00 0c  02  01  2e 2e 00 00              ".."       -> inode 2
    01 00 00 00  00 10  07  01  66 61 63 74 6f 72 79 00  "factory"  -> 0x01000000
    00 00 00 03  3f d8  08  01  69 6e 63 6f 6d 69 6e 67  "incoming" -> inode 3

The layout is ext2-shaped:

| offset | size | field |
|---|---|---|
| `+0x00` | 4 | inode number, big-endian |
| `+0x04` | 2 | record length, big-endian |
| `+0x06` | 1 | name length |
| `+0x07` | 1 | type; 1 = directory |
| `+0x08` | n | name, no terminator, padded so the record is a multiple of 4 |

`record length = 8 + round_up(name_len, 4)` holds for all four entries (1 and
2 give 12, 7 gives 16, 8 gives 16), and the LAST entry absorbs the remaining
space in the block: `incoming`'s `0x3fd8` runs from its own start to exactly
`0x4000`, which is the block size confirming itself a third time.

Root's `..` points at root, and `/incoming` -- the directory the firmware
drops transferred samples into -- is created by the format itself, which is
why it exists on a drive nobody has written to.

### The erase primitive

`FUN_400e2904(start_sector, count)` is what issues the commands the capture
recorded, and it settles the one detail a log cannot show on its own:

    cmd 0x23 (35, ERASE_GROUP_START) <- start * blocklen
    cmd 0x24 (36, ERASE_GROUP_END)   <- (count - 1 + start) * blocklen
    cmd 0x26 (38, ERASE)

The end address is `count - 1 + start`, so the erased range is **inclusive of
the end sector**. `emu/esdhc.py` models it that way.

## The firmware agrees, in its own UI

The last check needs no hexdump. `SETTINGS > SYSTEM > STORAGE` is the
firmware's own view of the drive, and on a formatted image it reads:

    STORAGE              RAM        +DRIVE
    PROJECTS: 1
    SOUNDS: 64            0%          0%
    SAMPLES: 81         FREE:       FREE:
    SAMPLE TIME:        64MB        959MB
    00:00/11:39

**959 MB free** is the number this document derives from the superblock two
sections up: 61,408 usable blocks of 16 KB is 959.5 MiB. That figure was
computed from the erase-region length and the allocator's block size, and the
device prints it without being asked. Before the SEC_COUNT fix there was no
formatted drive to report on at all.

## What the EXT_CSD model does and does not answer

Every reference into the 512-byte EXT_CSD buffer the controller DMAs to
(`0x4ba9f3e0`) was enumerated, so the list of fields this firmware reads is
exhaustive rather than incidental: ten offsets, `+0x098`, `+0x09c`-`+0x09e`,
`+0x0d4`-`+0x0d7`, `+0x0de`, `+0x0e3`, `+0x108`, `+0x10e` and `+0x10f`.

Only two of them decide anything. `+0x098` is the SLC flag the bring-up
insists on, and `+0x0d4` is the capacity everything above depends on. The rest
are read by three plain getters that fill an info struct for the console's MMC
commands and this STORAGE page, and the model leaves them zero **on purpose**:
a plausible-looking figure for a card nobody has read off real hardware would
be fabricated data, and zero at least says "unknown" honestly.

## Writing to it: the format, corrected and completed

`tools/ekfs.py` reads this filesystem. `tools/ekfsadd.py` writes to it, and
getting there corrected four things above.

**The superblock checksum is Bob Jenkins' lookup3, big-endian.**
`FUN_400d16d6(buf, len, seed)` sets three state words to `seed - 0x21524111`,
which is `seed + 0xdeadbeef`, streams the buffer through `FUN_400d1544` and
finalises in `FUN_400d1200`. The update rotations are 4, 6, 8, 16, 19, 4 and
the final rotations 14, 11, 25, 16, 4, 14, 24, which is lookup3 exactly; the
tail masks are `0xff000000`, `0xffff0000`, `0xffffff00`, which makes it
`hashbig` rather than `hashlittle`. One deliberate difference from the
reference: **the length is not folded into the seed.** The mount,
`FUN_400d0f9c`, refuses the volume unless the stored value matches, so this
has to be exact. Verified by recomputing `0xda3629df` on a formatted image.

**The inode holds an extent list, not two forks.** The table above reads
`+0x2c` as "second fork, size = 0x20000". It is not a size.
`FUN_400cecfc(inode, offset, length, flag)` converts a byte range to block
indices with `offset >> 14`, and a directory's initialiser asks for
`(0, 0x4000)` and `(0x80000000, 0xc000)`. `0x80000000 >> 14` is `0x20000`.
So the shape is:

| offset | field |
|---|---|
| `+0x00` | u8 type: **1 = directory, 0 = file** |
| `+0x01` | u8, always 2, written by the allocator `FUN_400d2290` |
| `+0x02` | u16 **link count** -- not an entry count. `FUN_400cd04c` never touches a directory's own `+0x02` on insert, only the target's when a directory is linked in, so a directory counts its `.`, its entry in its parent and each child's `..`: root 3, an empty `/incoming` 2. Read as an entry count, the two numbers happen to agree until the first file is added. |
| `+0x04` | u32 size in bytes |
| `+0x08` | u32 parent directory inode, for a file (and for the RAM factory directories); `3` on the formatted root and `/incoming` |
| `+0x0c` | u32 content hash `\| 1`, for a file -- see *Sample files* |
| `+0x10` | u32 serial, from a counter that only goes up |
| `+0x1e` | u16 **extent count** -- `FUN_400cea9a` returns `*(u16*)(inode+0x1e)` |
| `+0x20` | extents, 12 bytes each: u32 first logical block, u32 count, u32 first physical block |

Both `/` and `/incoming` read cleanly as two extents that way: logical 0 for
1 block, then logical `0x20000` for 3.

**Two field readings above are wrong.** `+0x08` is not "the inode's own
number": root is inode 2 and its `+0x08` reads 3, the same as `/incoming`'s.
And `+0x1c` is not a u32 parent inode, because `+0x1e` is the extent count;
what is left at `+0x1c` is a u16 that reads 0 on both.

**A file's type byte is 0.** `FUN_400cf178` is `open()`, and its create
branch allocates an inode and adds a directory entry and never writes a type
at all. `FUN_400d2290` zeroes all 128 bytes first, so a file is 0 and only
`FUN_400ccbf8` writes the 1 that marks a directory.

**A backup superblock sits just past the block bitmap.** The bitmap needs
7676 bytes for 61408 blocks and ends at `0x1dfc`; at offset `0x1e00` of the
block-bitmap region is a byte-identical copy, checksum included, so it has to
be rewritten whenever the primary is.

**The superblock's reservation fields.** `+0x24` = `0x10` and `+0x28` = `0x40`
are the two reservation sizes the format asks for, and `+0x2c` = `0x40` and
`+0x30` = `0x50` are where each run starts. The format reserves the first 96
blocks before allocating anything, which is why the device's own root lands
on block 96.

### Checked, not assumed

A filesystem built here from those constants was compared with the one the
firmware produced, sector by sector:

| region | result |
|---|---|
| superblock | identical, checksum included |
| inode bitmap | identical |
| inode table | identical |
| root directory block | identical |
| `/incoming` block | identical |
| block bitmap | identical apart from 5 bytes |

Those five bytes are `0x419a29dc` at offset `0x2000`, which is a task control
block address: uninitialised RAM that reached the reference image through the
emulator, not part of the format.

Writing a file and reading it back independently returns the same bytes, and
`tools/ekfs.py`, which knows nothing about the writer, lists it.


### What is not verified: the firmware mounting it

Everything above is checked offline. The firmware mounting one of these
images is not, and the reason is worth writing down.

The mount is `FUN_400d0f9c`. It reads the superblock, recomputes the
checksum, compares, and sets a flag at `0x420edc50` on success. Watching that
flag across 350M instructions with a card this tool made attached, it stays
0 -- but so does the command log: the firmware never reads sector `0x1c0000`
during boot at all. It does not mount the sample volume on the way up.

`FUN_400d0f9c` has exactly one caller, at `0x4006925c` inside
`FUN_40068eb6`. That is in the same code as the parked program counters of
the two tasks the scheduler walk reports as **linked into the ready
structure and never scheduled**, priority 0 at `0x41980928` parked at
`0x40068e34` and priority 1 at `0x4198497c` parked at `0x4006932e`. So the
task that would mount the sample filesystem is one that does not run under
this emulator.

That is a separate gap from anything in this document, and it bounds what
can be claimed here: the format is right as far as the firmware's own code
and its own formatted image can tell us, and no emulated device has yet been
seen to mount an image made by `tools/ekfsadd.py`.

## Still open

- The two unerased gaps, at 4-256 MB and 768-896 MB.
- Why the task that mounts the sample volume never gets scheduled. It
  is priority 0 or 1, both of which the scheduler walk reports as
  linked into the ready structure and never run.
- Whether real hardware reaches the firmware's big-endian read of SEC_COUNT
  through a byte-swapping controller, or whether this OS simply reads the
  field in the opposite order to the JEDEC spec. The two cannot be told apart
  without hardware, and the model behaves identically either way.
- The 512-byte record at sector 2048, written both by the boot job and by the
  format.
- The layout inside a 1 KB +Drive sound slot.
- The hashed name index in a directory's second fork.
- ~~How a FILE inode differs from a directory one.~~ Answered above: the
  type byte is 0 rather than 1, and the audio lives in the same extent list
  every inode uses. A 96 KB file written by `tools/ekfsadd.py` takes one
  extent of 6 blocks.
- ~~What the superblock's `+0x24` and `+0x28` count.~~ Answered above: they
  are the two reservation sizes, and `+0x2c`/`+0x30` are where each run
  starts. The original note follows. They are set
  around the two index-run allocations, so they are probably those runs'
  sizes, but that is a reading and not a measurement.
- Whether a project's 128-entry sample slot table (16 bytes per entry, at
  offset `0xec` in the project object; the loader walks slots 1..127) names
  an ekFS block directly or goes through the sound slots.
- Whether the sound and project regions carry their own superblocks. Both are
  erased by a format, but the mkfs recovered so far is the sample one.
