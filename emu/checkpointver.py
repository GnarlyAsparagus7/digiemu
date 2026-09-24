"""The snapshot format's version, importable without Unicorn.

emu/snapshot.py writes it into every snapshot and refuses a snapshot with a
different one. emu/bootstrap.py stamps it into every first-run stage, so an
app update that changes the format shows a folder as "needs setup" instead
of failing at the panel. The launcher asks that question for every folder it
lists (bootstrap.choose_snapshot), and importing emu.snapshot for one integer
loaded unicorn and emu.harness into the launcher process -- the Unicorn DLL
on the first refresh, and an import-time failure there marked every folder
broken. So the constant lives here, standard library only, and emu.snapshot
imports it.
"""

# Bump when the snapshot blob's layout changes incompatibly.
CHECKPOINT_VERSION = 2
