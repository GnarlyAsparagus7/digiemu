# pyright: reportMissingImports=false
"""Create a boot snapshot, and resume from one.

make:   uv run python -m emu.checkpoint make 60000000,400000000 [prefix] [syx]
        [--no-sdgate --no-esdhc]
resume: uv run python -m emu.checkpoint resume snapshots/boot400M.snap 5000000
"""

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unicorn.m68k_const import UC_M68K_REG_PC

import emu.dspboot as db
import emu.longrun as lr
from emu import config
from emu.bootstrap import replace_retry
from emu.snapshot import save


def _integer(text, label):
    try:
        return int(text)
    except (TypeError, ValueError) as exc:
        raise SystemExit("invalid %s: %r" % (label, text)) from exc


def _points(text):
    return [
        _integer(value, "checkpoint instruction count") for value in text.split(",")
    ]


def save_longrun(machine, ev, timers, path, extra=None):
    """Save stateful longrun state, including timer cadence.

    Example: ``save_longrun(m, ev, timers, '/tmp/run.snap', {'n': n})``.
    On restore, call ``ev['restore_checkpoint_timers']()`` before execution.
    ``make`` and timer-less ``resume`` remain legacy helpers and do not save
    timer cadence.
    """
    components = dict(ev["checkpoint_components"])
    components["timers"] = timers
    return save(
        machine,
        path,
        extra=extra,
        components=components,
        manifest=ev["checkpoint_manifest"],
    )


# dspboot.run's stop reason for a cold boot that used its whole budget. Any
# other reason is an emulator error string, and the ladder stopped early.
LIMIT_STOP = "instruction limit"


def make(points, prefix="snapshots/boot", syx=None, img_path=None,
         sdgate=True, esdhc=True, progress=None, report_every=5_000_000,
         out=None):
    """Save a LADDER of checkpoints in one pass.

    `points` is a list of instruction counts. Saving mid-run is safe because
    save() only reads state; emulation continues afterwards. One slow pass
    yields several resume points, so later blocker work can start deep.

    `sdgate`/`esdhc` install the board loopback of emu/gpio.py and the
    controller of emu/esdhc.py for the cold boot itself. They belong here
    rather than only on the longrun resume path because the continuity check
    that decides whether storage comes up at all runs at instruction
    64,164,269 on Digitone -- before the first rung at 60M -- so a ladder
    built without them bakes "no storage" into every rung, and no amount of
    enabling them at resume time can undo that.

    Both default to True now: without them the firmware's SD bring-up never
    runs, the storage-ready flag stays 0, and every block-storage read
    returns -1. With them on, both builds reach MAIN_OS_RUNNING under
    tools/bootcheck.py --verify -- Digitone's display module initialises for
    the first time, and Digitakt's cold boot creates 9 tasks instead of 5,
    including the priority-6 Main OS task at entry 0x40032f5a. Digitakt
    reaches MAIN_OS_RUNNING both with and without them, so turning them on
    does not regress the previously-working build. Pass sdgate=False and/or
    esdhc=False to get the old unmodelled-storage behaviour back.

    The prefix's directory is created first. The first rung is saved from
    inside the run at ~60M instructions, so a missing directory used to cost
    half a minute and then a FileNotFoundError. Each rung is written to
    `<rung>.tmp` and renamed into place, so a killed run never leaves a
    truncated rung that looks like a finished one. The renames retry while
    Windows refuses them because another process has the target open
    (emu.bootstrap.replace_retry).

    `progress`: None prints one line per rung, as this always has. Otherwise
    nothing is printed and it is called as ``progress(n, total, rung)``:
    every `report_every` instructions with rung=None, and once after each
    rung is saved with rung = {'at', 'path', 'addrs', 'tasks', 'pc', 'bytes',
    'text'} ('text' is the line that would have been printed). It may raise
    -- to cancel, say: Unicorn stops the run and the exception comes out of
    this call, with no sidecar written.

    `out`, a dict, receives 'm' (the Machine), 'st', 'saved' and 'stop' --
    dspboot.run's stop reason, LIMIT_STOP when the boot used its whole
    budget. A run that stopped early otherwise looks just like a finished
    one, because dspboot turns the emulator error into that string and
    returns normally. The sidecar (.ladder.json) is written only when every
    rung was saved.
    """
    syx = config.firmware(syx)
    image_path = config.main_image(img_path)
    try:
        with open(image_path, "rb") as image:
            img = image.read()
    except OSError as exc:
        raise RuntimeError("cannot read MAIN OS image %r" % image_path) from exc
    points = sorted(points)
    todo = list(points)
    box = out if out is not None else {}
    box.update({"m": None, "saved": [], "stop": None})
    limit = points[-1] + 1_000_000
    os.makedirs(os.path.dirname(prefix) or ".", exist_ok=True)
    # This hook runs on every instruction of the cold boot, so the common
    # case is one comparison against whichever comes first, the next rung or
    # the next progress report.
    never = 1 << 62
    next_report = report_every if progress is not None else never
    gate = min(next_report, todo[0])

    def hook(uc, addr, size, st):
        nonlocal next_report, gate
        if st["n"] < gate:
            return
        rung = todo and st["n"] >= todo[0]
        report = st["n"] >= next_report
        if report:
            next_report += report_every
        at = todo.pop(0) if rung else None
        gate = min(next_report, todo[0] if todo else never)
        if report:
            progress(st["n"], limit, None)
        if rung:
            path = "%s%dM.snap" % (prefix, at // 1_000_000)
            info = save(
                box["m"],
                path + ".tmp",
                extra={
                    "n": st["n"],
                    "seen": sorted(st["seen"]),
                    "tasks": {hex(k): v for k, v in st["task_create_hits"].items()},
                },
            )
            replace_retry(path + ".tmp", path)
            pc = uc.reg_read(UC_M68K_REG_PC)
            box["saved"].append(
                (
                    at,
                    path,
                    info,
                    len(st["seen"]),
                    len(st["task_create_hits"]),
                    pc,
                )
            )
            text = "  [%dM] %s  %d addrs, %d tasks, pc=0x%08x, %d B" % (
                at // 1_000_000,
                path,
                len(st["seen"]),
                len(st["task_create_hits"]),
                pc,
                info["bytes_on_disk"],
            )
            if progress is None:
                print(text, flush=True)
            else:
                progress(st["n"], limit, {
                    "at": at, "path": path, "addrs": len(st["seen"]),
                    "tasks": len(st["task_create_hits"]), "pc": pc,
                    "bytes": info["bytes_on_disk"], "text": text,
                })

    m, st, stop = db.run(
        syx,
        img,
        limit=limit,
        extra_hook=hook,
        fast=True,
        verbose=False,
        machine_out=box,
        sdgate=sdgate,
        esdhc=esdhc,
    )
    box["stop"] = stop
    if box["saved"] and not todo:
        # A snapshot carries no manifest on the cold-boot path -- save() is
        # called above without one -- so this sidecar is what lets a later
        # run (emu/run.py's need_snapshot) tell a ladder built with these
        # storage models from one built without them, instead of silently
        # resuming a mismatched configuration.
        from emu.run import ladder_config_path
        cfg_path = ladder_config_path(prefix)
        # pi-lens-ignore: ast-grep:unchecked-throwing-call-python
        os.makedirs(os.path.dirname(cfg_path) or ".", exist_ok=True)
        # pi-lens-ignore: ast-grep:unchecked-throwing-call-python
        with open(cfg_path + ".tmp", "w") as fh:
            json.dump({
                "protocol": 1,
                "sdgate": bool(sdgate),
                "esdhc": bool(esdhc),
                "points": points,
                "main_sha256": hashlib.sha256(img).hexdigest(),
            }, fh)
        replace_retry(cfg_path + ".tmp", cfg_path)
    elif todo and progress is None:
        print("  ladder incomplete: %d of %d rungs saved, stop=%s"
              % (len(box["saved"]), len(points), stop), flush=True)
    return box["saved"]


def resume(path, extra_instrs, hook=None, chunk=500_000):
    """Restore and run forward. Returns (machine, new_addrs, stop_reason, n).

    Resuming has to happen onto an ALREADY-hooked Machine: restoring onto a
    bare one drops the flash HLE, the completion-semaphore patch and the
    scheduler tick, and the run then diverges while still looking plausible
    (docs/history/upstream/NEXT.md trap 4). longrun.build does the hooking,
    so go through it rather than snapshot.restore().
    """
    from unicorn import UC_HOOK_CODE

    m, ev, st, pc, inq, at = lr.build(path)
    carried = set(st["seen"])
    if hook:
        m.uc.hook_add(UC_HOOK_CODE, hook)
    pc, done, stop = lr.spin(m, pc, extra_instrs, chunk)
    st["n"] += done
    return m, st["seen"] - carried, stop, done, st, ev


def extend(path, points, prefix="snapshots/ext", chunk=500_000):
    """Resume `path` and save a ladder of further checkpoints.

    `points` are instruction counts measured FROM the resume point, so
    extend('snapshots/boot280M.snap', [200e6, 400e6]) writes checkpoints at an
    absolute 480M and 680M. Coverage (`seen`) is carried through unchanged --
    tracking new coverage needs a global per-instruction hook, which costs ~3x
    and is not worth paying just to keep a statistic warm.
    """
    m, ev, st, pc, inq, at = lr.build(path)
    base_n = st["n"]
    todo, saved = sorted(points), []

    def on_chunk(p, done):
        while todo and done >= todo[0]:
            todo.pop(0)
            out = "%s%dM.snap" % (prefix, (base_n + done) // 1_000_000)
            extra = {
                "n": base_n + done,
                "seen": sorted(st["seen"]),
                "tasks": {hex(k): v for k, v in st["task_create_hits"].items()},
                "seen_stale": True,
            }
            info = save(m, out, extra=extra)
            saved.append((out, base_n + done))
            print(
                "  [%dM] %s  pc=0x%08x  %d B  tasks_seen_since=%d"
                % (
                    (base_n + done) // 1_000_000,
                    out,
                    p,
                    info["bytes_on_disk"],
                    len(ev["tasks"]),
                ),
                flush=True,
            )

    pc, done, stop = lr.spin(m, pc, max(points), chunk, on_chunk=on_chunk)
    return saved, m, ev, stop


if __name__ == "__main__":
    import time

    # Pulled out before the positional parse so they can be passed in any
    # position without disturbing the existing `make POINTS [prefix] [syx]`
    # argument order that emu/run.py relies on.
    argv = [a for a in sys.argv
            if a not in ("--sdgate", "--esdhc", "--no-sdgate", "--no-esdhc")]
    want_sdgate = "--no-sdgate" not in sys.argv
    want_esdhc = "--no-esdhc" not in sys.argv

    cmd = argv[1]
    if cmd == "make":
        pts = _points(argv[2])
        prefix = argv[3] if len(argv) > 3 else "snapshots/boot"
        syx = argv[4] if len(argv) > 4 else None
        make(pts, prefix, syx, sdgate=want_sdgate, esdhc=want_esdhc)
    elif cmd == "extend":
        snap = argv[2]
        pts = _points(argv[3])
        prefix = argv[4] if len(argv) > 4 else "snapshots/ext"
        t0 = time.time()
        saved, m, ev, stop = extend(snap, pts, prefix)
        print(
            "extended %s by %dM in %.0fs, stop=%s"
            % (snap, max(pts) // 1_000_000, time.time() - t0, stop)
        )
        print("new tasks: %s" % ["0x%08x/p%d" % (e, p) for e, p, _ in ev["tasks"]])
        print("prints   : %r" % ev["prints"][:20])
    else:
        path = argv[2]
        extra = (
            _integer(argv[3], "instruction count")
            if len(argv) > 3
            else 2_000_000
        )
        t0 = time.time()
        m, fresh, stop, n, st, ev = resume(path, extra)
        print(
            "resumed: ran %d instrs in %.1fs, %d NEW addrs, stop=%s pc=0x%08x"
            % (n, time.time() - t0, len(fresh), stop, m.uc.reg_read(UC_M68K_REG_PC))
        )
