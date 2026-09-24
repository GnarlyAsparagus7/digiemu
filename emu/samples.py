"""Put WAV files on a +Drive card as samples, in /incoming.

The panel's LOAD SAMPLES button (emu/dtpanel.py) is built on this, in two
halves, because the card may only be written once the emulator has let go of
it:

    plan(files, card)   reads the files and the card, never writes: safe
                        while the emulator runs. Decides each file's name on
                        the card and turns away what cannot go on it, so a
                        bad file is refused before anything is stopped.
    write(plan)         converts and writes them (emu/ekfsformat.py's sample
                        format, content hash and directory indexes), after
                        the emulator has stopped, flushed and closed the card.

The firmware indexes the card when it mounts it, during the cold boot, and
keeps that index in RAM -- so every snapshot predates what write() adds, and
the samples appear only after a rebuild (emu/bootstrap.py; ~13 s). The panel
hands that to the portable app, which rebuilds and reopens it.

Names. A file keeps its stem, as tools/ekfsadd.py does, cut to NAME_MAX
printable ASCII characters (anything else becomes '_'). NAME_MAX is a
cautious choice, not a limit read out of the firmware: the directory format
allows 255 bytes, and nothing checked yet shows what its UI and project code
take. A name the firmware would treat as the same as one already there --
FUN_400e8606 compares case-insensitively and skips the character after a
number, so 'Kick1a' equals 'KICK1b' -- gets '-2', '-3', ... instead.

The sample rate is kept, as ekfsadd keeps it: the header has a field for it
and the engine computes the pitch ratio from it (only 48 kHz has been
confirmed by ear; see HANDOFF-2026-09-22.md).
"""
import os
from dataclasses import dataclass, field

from emu import ekfsformat as ek

INCOMING = 'incoming'
NAME_MAX = 64
WAV_TYPES = [('WAV audio', '*.wav *.wave'), ('All files', '*.*')]


class Error(Exception):
    """The card as a whole cannot take samples (no ekFS, no /incoming)."""


@dataclass
class Sample:
    path: str
    name: str            # its name on the card
    rate: int
    frames: int
    blocks: int          # 16 KB blocks its sample file takes
    renamed: bool = False

    @property
    def seconds(self):
        return self.frames / self.rate if self.rate else 0.0


@dataclass
class Plan:
    card: str
    base: int = ek.REGION
    samples: list = field(default_factory=list)     # Sample
    rejected: list = field(default_factory=list)    # (path, reason)
    written: list = field(default_factory=list)     # (name, inode), by write()


def card_name(path):
    """-> the name `path` gets on the card, before any clash is resolved."""
    stem = ek.sample_name(path)
    name = ''.join(c if ' ' <= c <= '~' and c != '/' else '_' for c in stem)
    name = name.strip()[:NAME_MAX].rstrip()
    return name if name not in ('', '.', '..') else 'sample'


def _clashes(name, taken):
    key = name.encode('latin-1')
    return any(ek.natcmp(key, other) == 0 for other in taken)


def _free_name(name, taken):
    """-> (a name no entry in `taken` compares equal to, renamed?)."""
    base, k, out = name, 1, name
    while _clashes(out, taken):
        k += 1
        suffix = '-%d' % k
        out = base[:NAME_MAX - len(suffix)].rstrip() + suffix
    return out, out != name


def _incoming(fs):
    ino = ek.find_dir(fs, INCOMING)
    if ino is None:
        raise Error('the +Drive has no /%s folder' % INCOMING)
    return ino


def _sample_blocks(frames):
    size = ek.SAMPLE_HEADER + 2 * frames + ek.SAMPLE_TRAILER
    return max(1, -(-size // ek.BLOCK_BYTES))


def plan(files, card, base=ek.REGION):
    """-> Plan for putting `files` in the card's /incoming. Error when the
    card cannot take any (unreadable, no ekFS, no /incoming); a file that
    cannot go on it -- not a WAV this can convert, silent, or no room left
    -- is in Plan.rejected with the reason, the rest in Plan.samples."""
    out = Plan(card, base)
    try:
        fs = ek.Ekfs(card, base)
    except (OSError, ek.Error) as exc:
        raise Error('the +Drive image cannot be read: %s' % exc) from exc
    try:
        inc = _incoming(fs)
        entries = fs.dir_entries(inc)
        taken = [name for _loc, _ino, name, _typ in entries]
        # add_dir_entry puts every entry in the directory's first block.
        first = fs.extents(fs.inode(inc))[0][2]
        room = ek.BLOCK_BYTES - sum(ek.Ekfs._reclen(name) for
                                    _o, _r, _i, name, _t in
                                    fs.parse_dir(fs.block(first)))
        slots = ek.MAX_DIR_ENTRIES - len(entries)
        inodes = fs.inode_count - fs.used('inode')
        blocks = fs.block_count - fs.used('block')
    except ek.Error as exc:
        raise Error('the +Drive image is not usable: %s' % exc) from exc
    finally:
        fs.close()
    for path in files:
        try:
            with open(path, 'rb') as fh:
                info = ek.wav_info(fh.read())
        except OSError as exc:
            out.rejected.append((path, exc.strerror or str(exc)))
            continue
        except ek.Error as exc:
            out.rejected.append((path, str(exc)))
            continue
        if not info.frames:
            out.rejected.append((path, 'no audio in it'))
            continue
        name, renamed = _free_name(card_name(path), taken)
        need = _sample_blocks(info.frames)
        if slots < 1 or room < ek.Ekfs._reclen(name):
            out.rejected.append((path, 'the /%s folder is full' % INCOMING))
            continue
        if inodes < 1 or blocks < need:
            out.rejected.append((path, 'not enough room left on the +Drive'))
            continue
        slots, room = slots - 1, room - ek.Ekfs._reclen(name)
        inodes, blocks = inodes - 1, blocks - need
        taken.append(name.encode('latin-1'))
        out.samples.append(Sample(path, name, info.rate, info.frames, need,
                                  renamed))
    return out


def write(p, progress=None):
    """Convert and add every sample in Plan `p` to its card. -> p.written,
    [(name, inode)], which also holds what was added before a failure.

    Only once nothing else has the card open for writing. Names are checked
    again against the card as it is now: plan() may have read it while the
    firmware could still add to it. progress(sample), if given, is called
    before each one."""
    fs = ek.Ekfs(p.card, p.base, write=True)
    try:
        inc = _incoming(fs)
        taken = [name for _loc, _ino, name, _typ in fs.dir_entries(inc)]
        for s in p.samples:
            if progress is not None:
                progress(s)
            with open(s.path, 'rb') as fh:
                content, _rate, _frames = ek.wav_to_sample(fh.read())
            name, renamed = _free_name(s.name, taken)
            if renamed:
                s.name, s.renamed = name, True
            ino = fs.add_file(inc, name, content)
            taken.append(name.encode('latin-1'))
            p.written.append((name, ino))
        fs.f.flush()
    finally:
        fs.close()
    return p.written
