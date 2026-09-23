"""Resume a snapshot with its timer cadence intact.

Constructing a fresh `Dtims` REPAIRS stale guest DTMR registers -- which is
right after a cold boot and catastrophic when resuming a running system,
because it wipes the DTIM3 the firmware armed for itself. The symptom is a
snapshot whose framebuffer still holds a perfectly good user interface while
`dtim3_isr`, `pit3_isr` and `mainloop` all read zero: the picture is there,
nothing is executing. See emu/dtim.py's `restore_timers`.

So a snapshot that was taken mid-run must be resumed through
`ev['restore_checkpoint_timers']()`, which reconstructs the sources with
`clear_stale=False` and claims their saved cadence. A snapshot from the cold
ladder has no such state, and then fresh timers are the correct thing.
`open_snapshot` handles both and says which it did.
"""
from emu import longrun
from emu.dtim import Dtims, Timers
from emu.pit import Pits, intro_running


def open_snapshot(snapshot, syx, profile, channels=(1, 3), verbose=True,
                  **build_kwargs):
    """-> (m, ev, st, pc, inq, at, pits). Restores saved timers when present."""
    # build_kwargs must MATCH the flags the snapshot was saved with, or
    # restore_into rejects it on a build-manifest mismatch. emu/gui.py's
    # Emulator uses unblock/softfloat/bitmap/dsp=True, so a snapshot meant to
    # be opened by the GUI has to be made with those same flags.
    m, ev, st, pc, inq, at = longrun.build(
        snapshot, syx=syx, deferred_components=('timers',), **build_kwargs)

    pits = None
    restore = ev.get('restore_checkpoint_timers')
    if restore is not None:
        try:
            pits = restore()
        except Exception as exc:                        # noqa: BLE001
            if verbose:
                print('  timers: saved cadence unusable (%s), building fresh'
                      % exc)
            pits = None

    if pits is None:
        intro = intro_running(m, getattr(profile, 'intro_pit3_isr', None))
        pits = Timers(Pits(m, hold=intro),
                      Dtims(m, channels=channels, hold=intro))
        if verbose:
            print('  timers: fresh (cold-ladder snapshot)')
    elif verbose:
        print('  timers: restored saved cadence')

    return m, ev, st, pc, inq, at, pits
