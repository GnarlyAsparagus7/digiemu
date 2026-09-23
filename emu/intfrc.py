# pyright: reportMissingImports=false
"""Software-forced interrupts on INTC0: the Digitakt mk1 sequencer tick.

Each INTC has a pair of force registers, INTFRCH (sources 32-63) at +0x10 and
INTFRCL (sources 0-31) at +0x14. Setting a bit asserts that source as if its
peripheral had, and it stays asserted until software clears the bit (MCF54418
RM chapter 17). Firmware uses them as software interrupts at a chosen level.

The mk1 sequencer runs on two, chained:

- INTC0 source 44 has no peripheral (RM Table 17-15: "Not used"), yet OS 1.53
  gives it ICR level 5 and vector 108 a handler at `0x4006e756`. The audio
  render (vector 191) counts samples down in `[0x80001f54]`; at zero it saves
  the remainder, parks the count at `0x7fffffff` and sets INTFRCH0 bit 12 at
  `0x40078406`. The handler clears the bit first thing
  (`and.l #$ffffefff,$fc048010`) and runs the sequencer tick.
- That handler in turn sets INTFRCH0 bit 25 at `0x4006eae4`: source 57,
  vector 121, level 2, handler `0x4007041c`, which does the slower work.

Nothing modelled either, so the tick never ran: PLAY set the transport flags
and the pattern never advanced, the render having forced one tick and then
parked its countdown waiting for the sequencer to reload it.

The model: a forced source is asserted while its bit is set, and delivered at
the first step boundary where the IPL allows its level -- for source 44, just
after the render's `rte`, since both run at level 5. While one waits, steps
are cut to PENDING_STEP so it is not left until the next timer. It is
delivered once per assertion; the guest's clear re-arms it. Every INTC0
source is watched, and one whose ICR level is 0 is never delivered, as on the
hardware.

INTC1's source 63 (the render itself, forced from the SSI interrupt) is
modelled in emu/ssi.py, which delivers it from the forcing ISR's `rte`; INTC2's
reschedule bits are not modelled here.
"""
import collections

from unicorn import UC_HOOK_MEM_WRITE
from unicorn.m68k_const import UC_M68K_REG_SR

from emu.pit import PENDING_STEP, interrupt_level, render_holds

INTC0, FIRST_VECTOR = 0xFC048000, 64
INTFRCH, INTFRCL = 0x10, 0x14


class ForcedInterrupts:
    """Deliver INTC0 software-forced sources, on the shared step clock."""

    def __init__(self, m, sources=tuple(range(64)), base=INTC0,
                 first_vector=FIRST_VECTOR):
        self.m = m
        self.base = base
        self.sources = tuple(sources)
        self.first_vector = first_vector
        self.asserted = set()
        self.delivered = set()
        self.fired = collections.Counter()
        m.uc.hook_add(UC_HOOK_MEM_WRITE, self._on_write,
                      begin=base + INTFRCH, end=base + INTFRCL + 3)

    def _forced(self, regs):
        """-> the watched sources set in the 8 bytes INTFRCH..INTFRCL."""
        hi = int.from_bytes(regs[0:4], 'big')
        lo = int.from_bytes(regs[4:8], 'big')
        return {s for s in self.sources
                if (hi >> (s - 32) if s >= 32 else lo >> s) & 1}

    def _on_write(self, uc, access, address, size, value, data):
        # The hook runs before the store lands: merge it in by hand.
        regs = bytearray(uc.mem_read(self.base + INTFRCH, 8))
        off = address - (self.base + INTFRCH)
        regs[off:off + size] = int(value & ((1 << (8 * size)) - 1)).to_bytes(
            size, 'big')
        now = self._forced(regs)
        # A source cleared (the handler's ack) or newly set is re-armed.
        self.delivered &= now & self.asserted
        self.asserted = now

    def step(self, done, remaining=None):
        waiting = self.asserted - self.delivered
        if not waiting:
            return None
        # Waiting out the audio render (source 57 behind it, typically):
        # its rte ends the step, so there is nothing to poll for.
        if all(render_holds(self.m, done, interrupt_level(
                self.m, self.first_vector + src, respect_mask=False))
               for src in waiting):
            return remaining
        return min(PENDING_STEP, remaining) if remaining is not None \
            else PENDING_STEP

    def service(self, done):
        waiting = self.asserted - self.delivered
        if not waiting:
            return False
        taken = False
        ready = []
        for src in waiting:
            # A forced source bypasses the mask registers (as in emu/ssi.py).
            lvl = interrupt_level(self.m, self.first_vector + src,
                                  respect_mask=False)
            if lvl is None:
                # Level 0 never interrupts. Retire this assertion rather than
                # cutting every step short while the bit stays set.
                self.delivered.add(src)
            else:
                ready.append((lvl, src))
        # Highest level first; within a level the INTC takes the highest
        # source number. Taking one raises the IPL, which holds the rest.
        for lvl, src in sorted(ready, reverse=True):
            vec = self.first_vector + src
            sr = self.m.uc.reg_read(UC_M68K_REG_SR)
            if ((sr >> 8) & 0x07) >= lvl:
                continue
            if self.m.raise_vector(vec, level=lvl):
                self.delivered.add(src)
                self.fired[src] += 1
                taken = True
        return taken

    def checkpoint_state(self):
        return {'type': 'ForcedInterrupts', 'version': 1,
                'sources': list(self.sources), 'asserted': sorted(self.asserted),
                'delivered': sorted(self.delivered),
                'fired': {str(k): v for k, v in self.fired.items()}}

    def restore_checkpoint_state(self, state):
        if state.get('type') != 'ForcedInterrupts' or state.get('version') != 1:
            raise RuntimeError('unsupported ForcedInterrupts checkpoint state')
        if tuple(state['sources']) != self.sources:
            raise RuntimeError('ForcedInterrupts source mismatch')
        self.asserted = set(state['asserted'])
        self.delivered = set(state['delivered'])
        self.fired = collections.Counter(
            {int(k): v for k, v in state['fired'].items()})


def install(machine, events, **kwargs):
    """Watch INTC0's force registers. The current bits are read at once, so a
    snapshot taken with a source already forced delivers it."""
    source = ForcedInterrupts(machine, **kwargs)
    regs = bytes(machine.uc.mem_read(source.base + INTFRCH, 8))
    source.asserted = source._forced(regs)
    events['intfrc0'] = source
    return source
