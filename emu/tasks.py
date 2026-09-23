"""Where is every task parked?

Only one task runs at a time, so hooking the sem_pend entry never sees the
tasks that are *already* blocked -- they are sitting inside the pend, not
entering it. Their state is in their TCB instead.

The context switcher at 0x40000410 gives the layout:

    movea.l  $47d9adb4,a0          ; a0 = current TCB
    movem.l  d0-d7/a0-a7,$c(a0)    ; registers at TCB+0x0C ..
    move.l   -4(a7),$2c(a0)        ; .. so a0 is at +0x2C and a7 at +0x48
    movea.l  $4094c914,a1          ; ready-list cursor
    movea.l  (a1),a0 ; movea.l (a0),a0   ; TCB+0x00 is the next pointer
    movem.l  $c(a0),d0-d7/a0-a7 ; rte

so a parked task's PC is on its own stack: ColdFire pushes a two-longword
exception frame, [a7] = format/vector/SR and [a7+4] = PC.

The two literal operands above, $47d9adb4 and $4094c914, are exactly what
`symbols.current_tcb` / `symbols.ready_cursor` read out of the switcher, so
they are resolved per build in emu/symbols.py rather than hardcoded here.

Usage: python -m emu.tasks <snapshot>
"""
import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from emu.snapshot import restore
from emu import config, symbols

REG_BASE = 0x0C                 # d0-d7 then a0-a7
A7_OFF   = REG_BASE + 15 * 4    # 0x48
NEXT_OFF = 0x00
PREV_OFF = 0x04
PRIO_SLOT_OFF = 0x08   # -> &prio_heads[priority]; see task_create


def u32(m, a):
    try: return struct.unpack('>I', m.uc.mem_read(a, 4))[0]
    except Exception: return None


def parked_pc(m, tcb):
    """-> (saved a7, frame word, pc) for a task that is not currently running."""
    sp = u32(m, tcb + A7_OFF)
    if not sp: return sp, None, None
    return sp, u32(m, sp), u32(m, sp + 4)


def ready_list(m, ready_cursor, limit=32):
    """Walk the ready list from the cursor. -> list of TCB addresses."""
    cur = u32(m, ready_cursor)
    if not cur: return []
    out, node, seen = [], u32(m, cur), set()
    while node and node not in seen and len(out) < limit:
        seen.add(node)
        out.append(node)
        node = u32(m, node + NEXT_OFF)
    return out


def priority_lists(m, prio_heads, current_tcb, limit=64):
    """Every task the scheduler knows about, by walking the priority array.

    -> [(priority, [TCB, ...]), ...] for each non-empty slot, low index first.

    This is what makes the listing work without snapshot metadata. A
    snapshot only carries a `tasks` map if the run that produced it watched
    task_create go by, so resuming a snapshot made any other way -- which is
    every Digitakt mk1 one -- left the old listing with nothing to print.
    The scheduler's own array does not depend on having watched anything.

    Each slot holds the head of a CIRCULAR doubly-linked list: pend_b
    unlinks with `next->prev = prev; prev->next = next` and recognises "last
    one in this list" as `next == self`, so the walk stops when it returns to
    a node it has seen, not on a null.

    The array ends where `current_tcb` begins; see emu/symbols.py. Slots are
    bounded by that rather than by a guessed count.
    """
    if not prio_heads or not current_tcb:
        return []
    span = current_tcb - prio_heads
    if not 0 < span <= 0x400:
        return []
    out = []
    for p in range(span // 4):
        head = u32(m, prio_heads + 4 * p)
        if not head:
            continue
        chain, node, seen = [], head, set()
        while node and node not in seen and len(chain) < limit:
            seen.add(node)
            chain.append(node)
            node = u32(m, node + NEXT_OFF)
        out.append((p, chain))
    return out


def prio_of(m, tcb, prio_heads):
    """A task's priority, from the head-slot pointer it carries at +8."""
    slot = u32(m, tcb + PRIO_SLOT_OFF)
    if not slot or not prio_heads or slot < prio_heads:
        return None
    return (slot - prio_heads) // 4


def main(snap):
    m, extra, regs = restore(snap)
    tasks = extra.get('tasks', {})
    main_img = open(config.main_image(), 'rb').read()
    profile = symbols.resolve(main_img)
    print('snapshot %s   n=%s' % (snap, extra.get('n')))
    cur, rl = None, []
    if profile.current_tcb is None or profile.ready_cursor is None:
        print('scheduler variables (current_tcb/ready_cursor) did not '
              'resolve for this image; skipping current-task and ready-list output')
    else:
        cur = u32(m, profile.current_tcb)
        print('current TCB 0x%08x   live pc=0x%08x sr=0x%04x' % (cur, regs['pc'], regs['sr']))
        rl = ready_list(m, profile.ready_cursor)
        print('ready list (%d nodes): %s' % (len(rl), ['0x%08x' % t for t in rl]))

    print('\n%-11s %-5s %-11s %-11s %-11s %s'
          % ('tcb', 'prio', 'entry', 'saved a7', 'parked pc', 'state'))
    rows = sorted(tasks.values(), key=lambda i: i['prio'])
    for info in rows:
        tcb = info['tcb']
        if tcb == cur:
            print('  0x%08x %-5d 0x%08x %-11s 0x%08x  RUNNING'
                  % (tcb, info['prio'], info['entry'], '-', regs['pc']))
            continue
        sp, frame, pc = parked_pc(m, tcb)
        print('  0x%08x %-5d 0x%08x 0x%08x  %s  %s'
              % (tcb, info['prio'], info['entry'], sp or 0,
                 ('0x%08x' % pc) if pc else '    ?     ',
                 'ready' if tcb in rl else 'blocked'))

    # The scheduler's own view. Printed always, and it is the ONLY output
    # when `tasks` is empty, which is every snapshot whose run did not watch
    # task_create -- including all the mk1 ones.
    lists = priority_lists(m, profile.prio_heads, profile.current_tcb)
    if not lists:
        if profile.prio_heads is None:
            print('\nprio_heads did not resolve for this image; no scheduler walk')
        return
    slots = (profile.current_tcb - profile.prio_heads) // 4
    print('\nscheduler priority lists (base 0x%08x, %d slots; a higher index '
          'runs first)' % (profile.prio_heads, slots))
    print('  %-5s %-11s %-11s %-11s %s'
          % ('prio', 'tcb', 'saved a7', 'parked pc', 'state'))
    for prio, chain in lists:
        for tcb in chain:
            if tcb == cur:
                print('  %-5d 0x%08x %-11s 0x%08x  RUNNING'
                      % (prio, tcb, '-', regs['pc']))
                continue
            sp, frame, pc = parked_pc(m, tcb)
            print('  %-5d 0x%08x 0x%08x  %s  %s'
                  % (prio, tcb, sp or 0,
                     ('0x%08x' % pc) if pc else '    ?     ',
                     'ready' if tcb in rl else 'blocked'))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'snapshots/ext1080M.snap')
