"""A timer tick the CPU's IPL refuses waits, as PIF/REF hold it on hardware.

Before this, a refused tick was dropped. Under live audio the mk1 render holds
the IPL above DTIM3's level for most of each pass, so most main-loop ticks were
lost and the UI ran at a quarter of its rate.
"""
import struct
import unittest

from unicorn.m68k_const import UC_M68K_REG_A7, UC_M68K_REG_PC, UC_M68K_REG_SR

from emu import dtim, pit
from emu.harness import VBR, Machine

HANDLER = 0x40100000
STACK = 0x40200000


def _machine(vec, level, sr):
    m = Machine()
    for addr in (VBR, HANDLER, STACK - 0x100):
        m.ensure(addr)
    m.uc.mem_write(VBR + vec * 4, struct.pack('>I', HANDLER))
    for base, first in pit.INTC:
        if first <= vec < first + 64:
            m.ensure(base)
            m.uc.mem_write(base + pit.ICR_BASE + vec - first, bytes([level]))
    m.uc.reg_write(UC_M68K_REG_SR, sr)
    m.uc.reg_write(UC_M68K_REG_A7, STACK)
    m.uc.reg_write(UC_M68K_REG_PC, 0x40001000)
    return m


def _dtim3(sr=0x2700):
    m = _machine(dtim.VECTORS[3], 2, sr)
    base = dtim.BASES[3]
    m.ensure(base)
    dtmr = dtim.RST | dtim.FRR | dtim.ORRI | (2 << 1)     # bus clock / 16
    m.uc.mem_write(base + dtim.DTMR, struct.pack('>H', dtmr))
    m.uc.mem_write(base + dtim.DTRR, struct.pack('>I', 999))
    return m, dtim.Dtims(m, channels=(3,), clear_stale=False)


def _pit2(sr=0x2700):
    m = _machine(pit.VECTORS[2], 3, sr)
    m.ensure(pit.BASES[2])
    m.uc.mem_write(pit.BASES[2], struct.pack('>HH', pit.EN | pit.PIE, 999))
    return m, pit.Pits(m, channels=(2,))


class PendingTickTest(unittest.TestCase):
    def check_waits_then_fires(self, m, timers, ch):
        timers.service(0)                        # arms the clock
        due = timers.next[ch]
        timers.service(due)
        self.assertEqual(timers.pending, {ch})
        self.assertEqual((timers.fired[ch], timers.missed[ch]), (0, 0))
        self.assertLessEqual(timers.step(due), pit.PENDING_STEP)

        m.uc.reg_write(UC_M68K_REG_SR, 0x2000)   # the mask drops
        timers.service(due + 10)
        self.assertEqual(timers.pending, set())
        self.assertEqual(timers.fired[ch], 1)
        self.assertEqual(m.uc.reg_read(UC_M68K_REG_PC), HANDLER)

    def check_merges_and_disables(self, m, timers, ch, off):
        timers.service(0)
        p = timers.next[ch]
        timers.service(p)
        timers.service(2 * p)                    # due again while waiting
        self.assertEqual(timers.pending, {ch})
        self.assertEqual(timers.missed[ch], 1)
        off()
        timers.service(2 * p + 1)
        self.assertEqual(timers.pending, set())
        self.assertEqual(timers.fired[ch], 0)

    def test_dtim_tick_waits_for_the_mask(self):
        m, d = _dtim3()
        self.check_waits_then_fires(m, d, 3)

    def test_pit_tick_waits_for_the_mask(self):
        m, p = _pit2()
        self.check_waits_then_fires(m, p, 2)

    def test_dtim_waiting_tick_merges_and_dies_with_the_timer(self):
        m, d = _dtim3()
        self.check_merges_and_disables(
            m, d, 3, lambda: m.uc.mem_write(dtim.BASES[3] + dtim.DTMR, b'\0\0'))

    def test_pit_waiting_tick_merges_and_dies_with_the_timer(self):
        m, p = _pit2()
        self.check_merges_and_disables(
            m, p, 2, lambda: m.uc.mem_write(pit.BASES[2], b'\0\0'))

    def test_unmasked_tick_is_taken_at_its_deadline(self):
        m, d = _dtim3(sr=0x2000)
        d.service(0)
        d.service(d.next[3])
        self.assertEqual((d.fired[3], d.pending), (1, set()))


if __name__ == '__main__':
    unittest.main()
