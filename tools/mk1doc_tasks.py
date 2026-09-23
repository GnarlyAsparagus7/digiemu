#!/usr/bin/env python3
"""The RTOS task inventory, captured during a real boot.

`python -m emu.tasks` walks the ready list, which on this firmware holds one
node -- everything else is blocked on something and so is not on it. That is
why it prints almost nothing here. The full inventory has to be captured as
the tasks are CREATED, which means running the boot rather than reading a
snapshot.

So this boots from a ladder rung using the mk1 intro policy (PIT3 delivered,
frame semaphore left alone -- see devices/digitakt.toml), records every task
creation, and then reads each task's parked PC from its own saved a7 using the
TCB layout emu/tasks.py derives from the context switcher.

A parked PC of 0x4000176c or 0x400016fa is NORMAL: 0x4000176a is trap #0, the
scheduler's context-switch trap, so those are resume points inside a pend
rather than anything being stuck.

    python tools/mk1doc_tasks.py --syx Digitakt_OS1.53.syx --out docs/mk1
"""
import argparse
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from emu import config, longrun, symbols, tasks as tasklib
from emu.dtim import Dtims, Timers
from emu.pit import Pits

INTRO_DONE = 0x4006CB94


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--syx', required=True)
    ap.add_argument('--snapshot',
                    default='snapshots/Digitakt_OS1.53/boot400M.snap')
    ap.add_argument('--budget', type=lambda s: int(s, 0), default=160_000_000)
    ap.add_argument('--chunk', type=lambda s: int(s, 0), default=20_000_000)
    ap.add_argument('--out', default='docs/mk1')
    args = ap.parse_args()
    os.makedirs(os.path.join(args.out, 'facts'), exist_ok=True)

    img = open(config.main_image(), 'rb').read()
    prof = symbols.resolve(img, load_addr=0x40000400)

    m, ev, st, pc, inq, at = longrun.build(
        args.snapshot, syx=args.syx, unblock=True, softfloat=True,
        bitmap=True, dsp=True, unblock_except=(prof.frame_sem,),
        deferred_components=('timers',))

    pit = Pits(m, channels=(3,), hold=False)
    dtim = Dtims(m, channels=(1, 3), hold=True)
    pits = Timers(pit, dtim)
    skip = ev.get('unblock_skip')
    done = {'v': 0}

    def handover(*_a):
        if done['v']:
            return
        done['v'] = 1
        if skip is not None:
            skip.add(prof.frame_sem)
        pit.channels = (3, 2, 0)
        pits.release()

    at(INTRO_DONE, handover)

    total = 0
    while total < args.budget:
        pc, _e, _w = longrun.spin(m, pc, args.chunk, pits=pits)
        total += args.chunk
        print('  %dM  tasks so far: %d'
              % (total // 1_000_000, len(ev.get('tasks', ()))), flush=True)

    raw_tasks = ev.get('tasks', ()) or ()
    rows = []
    for t in raw_tasks:
        # ev['tasks'] entries are dicts or tuples depending on version.
        if isinstance(t, dict):
            entry = t.get('entry')
            prio = t.get('prio')
            tcb = t.get('tcb')
        else:
            entry, prio, tcb = (list(t) + [None, None, None])[:3]
        pcv = None
        if tcb:
            try:
                # parked_pc returns (saved a7, frame word, pc) -- a 3-tuple,
                # not a scalar. Unpacking it wrongly silently yields None for
                # every task, which looks like "nothing is parked".
                _sp, _frame, pcv = tasklib.parked_pc(m, tcb)
            except Exception:                            # noqa: BLE001
                pcv = None
        rows.append({
            'entry': '0x%08X' % entry if isinstance(entry, int) else str(entry),
            'priority': prio,
            'tcb': '0x%08X' % tcb if isinstance(tcb, int) else str(tcb),
            'parked_pc': '0x%08X' % pcv if isinstance(pcv, int) and pcv
                         else None,
        })
    rows.sort(key=lambda r: (r['priority'] is None, r['priority']))

    # ev['tasks'] only holds tasks created DURING this run, so a boot resumed
    # from a rung misses everything created before the rung was captured.
    #
    # This used to start at current_tcb and follow the next pointer at +0x00.
    # Every priority slot is its own CIRCULAR list, so that walk only ever
    # enumerated the running task's own slot and stopped as soon as it came
    # back around -- one entry, whenever the running task is alone at its
    # priority, reported as "what is actually present".
    # emu/tasks.priority_lists walks the scheduler's whole array instead.
    chain = []
    try:
        cur0 = struct.unpack('>I', m.uc.mem_read(prof.current_tcb, 4))[0] \
            if prof.current_tcb else 0
        for prio, tcbs in tasklib.priority_lists(
                m, prof.prio_heads, prof.current_tcb):
            for node in tcbs:
                # A parked PC is only meaningful for a task that is PARKED.
                # The running task's a7 points into its live stack, and
                # reading a return address off it yields a plausible-looking
                # number that means nothing.
                if node == cur0:
                    ppc = None
                else:
                    try:
                        _sp, _f, ppc = tasklib.parked_pc(m, node)
                    except Exception:                    # noqa: BLE001
                        ppc = None
                chain.append({'tcb': '0x%08X' % node,
                              'priority': prio,
                              'parked_pc': '0x%08X' % ppc if ppc else None,
                              'is_current': node == cur0})
    except Exception as exc:                             # noqa: BLE001
        chain = [{'error': '%s: %s' % (type(exc).__name__, exc)}]

    cur = None
    if prof.current_tcb:
        try:
            cur = struct.unpack('>I', m.uc.mem_read(prof.current_tcb, 4))[0]
        except Exception:                                # noqa: BLE001
            cur = None

    facts = {
        'document': 'digitakt-mk1.tasks',
        'schema_version': 1,
        'method': 'captured during a boot from %s over %dM instructions, '
                  'using the mk1 intro policy'
                  % (os.path.basename(args.snapshot),
                     args.budget // 1_000_000),
        'tcb_layout': {
            'next_pointer': '+0x00',
            'registers_d0_d7_a0_a7': '+0x0C',
            'saved_a7': '+0x48',
            'source': 'derived from the context switcher at 0x40000410; see '
                      'emu/tasks.py',
        },
        'current_tcb': '0x%08X' % cur if cur else None,
        'task_count': len(rows),
        'tasks': rows,
        'capture_limitation': "ev['tasks'] records only tasks CREATED during "
                              'this run. A boot resumed from a ladder rung '
                              'misses every task created before that rung was '
                              'captured, so this list is not the whole system.',
        'tcb_chain_walk': chain,
    }
    jpath = os.path.join(args.out, 'facts', 'tasks.json')
    with open(jpath, 'w', encoding='utf-8') as fh:
        json.dump(facts, fh, indent=2)
        fh.write('\n')
    print('wrote %s' % jpath)

    L = []
    A = L.append
    A('# 05 — RTOS tasks')
    A('')
    A('Generated by `tools/mk1doc_tasks.py`. Machine-readable:')
    A('`facts/tasks.json`.')
    A('')
    A('`python -m emu.tasks` walks the **ready list**, which on this firmware')
    A('holds a single node — everything else is blocked on something and so')
    A('is not on it. That is why it prints almost nothing here. The full')
    A('inventory has to be captured as tasks are **created**, which means')
    A('running the boot rather than reading a snapshot.')
    A('')
    A('Captured over %dM instructions from `%s`.'
      % (args.budget // 1_000_000, os.path.basename(args.snapshot)))
    A('')
    A('## TCB layout')
    A('')
    A('Derived from the context switcher at `0x40000410` — see')
    A('`emu/tasks.py`, which reads the layout out of the instruction stream')
    A('rather than assuming it.')
    A('')
    A('| field | offset |')
    A('|---|---|')
    A('| next pointer | `+0x00` |')
    A('| d0–d7 then a0–a7 | `+0x0C` |')
    A('| saved a7 | `+0x48` |')
    A('')
    A('## Tasks (%d)' % len(rows))
    A('')
    if not rows:
        A('None captured — see `facts/tasks.json`.')
    else:
        A('| priority | entry | TCB | parked PC |')
        A('|---|---|---|---|')
        for r in rows:
            A('| %s | `%s` | `%s` | %s |'
              % (r['priority'], r['entry'], r['tcb'],
                 ('`%s`' % r['parked_pc']) if r['parked_pc'] else '—'))
    A('')
    A('> **This is not the whole system.** `ev[\'tasks\']` records only tasks')
    A('> *created during this run*, and this boot resumes from a ladder rung,')
    A('> so every task created before that rung was captured is missing.')
    A('')
    A('## Scheduler priority walk')
    A('')
    A("Every task the scheduler knows about, read from its own priority")
    A('array rather than from what this run happened to watch being')
    A('created. Each slot is a circular doubly-linked list, so the walk')
    A('stops when it returns to a node it has seen rather than on a null.')
    A('Entry points are only known for tasks caught being created.')
    A('')
    if chain and 'error' not in chain[0]:
        A('| priority | TCB | parked PC | current |')
        A('|---|---|---|---|')
        for c in chain:
            A('| %s | `%s` | %s | %s |'
              % ('—' if c.get('priority') is None else c['priority'],
                 c['tcb'],
                 'running, not parked' if c['is_current']
                 else (('`%s`' % c['parked_pc']) if c['parked_pc']
                       else '—'),
                 'yes' if c['is_current'] else ''))
        A('')
        A('%d task(s) across %d priority slot(s).'
          % (len(chain), len({c.get('priority') for c in chain})))
    else:
        A('Priority walk failed: %s'
          % (chain[0].get('error') if chain else 'no current TCB'))
    A('')
    A('Current TCB at capture: `%s`.' % (facts['current_tcb'] or 'unknown'))
    A('')
    A('A parked PC of `0x4000176C` or `0x400016FA` is **normal**:')
    A('`0x4000176A` is `trap #0`, the scheduler\'s context-switch trap, so')
    A('those are resume points inside a pend rather than anything stuck.')
    A('')

    mpath = os.path.join(args.out, '05-rtos.md')
    with open(mpath, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L) + '\n')
    print('wrote %s (%d tasks)' % (mpath, len(rows)))


if __name__ == '__main__':
    main()
