"""INTC0 software-forced interrupts (emu/intfrc.py): the mk1 sequencer tick.

The render forces INTC0 source 44 (vector 108) by setting INTFRCH0 bit 12, and
the handler forces source 57 (vector 121). Undelivered, PLAY ran no pattern.
"""
import struct
import unittest

from unicorn.m68k_const import UC_M68K_REG_A7, UC_M68K_REG_PC, UC_M68K_REG_SR

from emu import intfrc, pit
from emu.harness import VBR, Machine

CODE = 0x40001000
HANDLERS = {108: 0x40100000, 121: 0x40100100}
STACK = 0x40200000
INTFRCH0 = intfrc.INTC0 + intfrc.INTFRCH


def _machine(sr=0x2000, levels=((44, 5), (57, 2))):
    m = Machine()
    for addr in (VBR, CODE, HANDLERS[108], STACK - 0x100, intfrc.INTC0):
        m.ensure(addr)
    for vec, handler in HANDLERS.items():
        m.uc.mem_write(VBR + vec * 4, struct.pack('>I', handler))
    for src, lvl in levels:
        m.uc.mem_write(intfrc.INTC0 + pit.ICR_BASE + src, bytes([lvl]))
    m.uc.reg_write(UC_M68K_REG_SR, sr)
    m.uc.reg_write(UC_M68K_REG_A7, STACK)
    return m


def _guest_or(m, value):
    """Run `move.l #value,d0; or.l d0,INTFRCH0` as guest code."""
    m.uc.mem_write(CODE, b'\x20\x3c' + struct.pack('>I', value)
                   + b'\x81\xb9' + struct.pack('>I', INTFRCH0))
    m.uc.emu_start(CODE, CODE + 12)


def _guest_clear(m, bit):
    """Run `move.l #~(1<<bit),d0; and.l d0,INTFRCH0` as guest code."""
    m.uc.mem_write(CODE, b'\x20\x3c' + struct.pack('>I', ~(1 << bit) & 0xFFFFFFFF)
                   + b'\xc1\xb9' + struct.pack('>I', INTFRCH0))
    m.uc.emu_start(CODE, CODE + 12)


class ForcedInterruptTest(unittest.TestCase):
    def test_guest_force_is_delivered_once_until_cleared(self):
        m = _machine()
        f = intfrc.install(m, {})
        self.assertIsNone(f.step(0))
        _guest_or(m, 1 << 12)                        # the render's force
        self.assertEqual(f.asserted, {44})
        self.assertEqual(f.step(0), pit.PENDING_STEP)
        self.assertTrue(f.service(0))
        self.assertEqual(m.uc.reg_read(UC_M68K_REG_PC), HANDLERS[108])
        self.assertEqual(f.fired[44], 1)
        self.assertIsNone(f.step(0))                 # taken; bit still set
        self.assertFalse(f.service(0))
        m.uc.reg_write(UC_M68K_REG_SR, 0x2000)
        _guest_clear(m, 12)                          # the handler's ack
        self.assertEqual(f.asserted, set())
        _guest_or(m, 1 << 12)                        # the next tick
        self.assertTrue(f.service(0))
        self.assertEqual(f.fired[44], 2)

    def test_waits_for_the_ipl(self):
        m = _machine(sr=0x2500)                      # the render's level
        f = intfrc.install(m, {})
        _guest_or(m, 1 << 12)
        m.uc.reg_write(UC_M68K_REG_SR, 0x2500)
        self.assertFalse(f.service(0))
        self.assertEqual(f.step(0, 100), 100)
        m.uc.reg_write(UC_M68K_REG_SR, 0x2400)       # after its rte
        self.assertTrue(f.service(0))

    def test_the_second_stage_and_its_lower_level(self):
        m = _machine(sr=0x2000)
        f = intfrc.install(m, {})
        _guest_or(m, (1 << 12) | (1 << 25))          # sources 44 and 57
        f.service(0)
        # 44 is level 5 and is taken first; 57 (level 2) waits under it.
        self.assertEqual(m.uc.reg_read(UC_M68K_REG_PC), HANDLERS[108])
        self.assertEqual((f.fired[44], f.fired[57]), (1, 0))
        m.uc.reg_write(UC_M68K_REG_SR, 0x2000)
        f.service(0)
        self.assertEqual(m.uc.reg_read(UC_M68K_REG_PC), HANDLERS[121])
        self.assertEqual(f.fired[57], 1)

    def test_level_zero_source_is_retired_not_polled(self):
        m = _machine(levels=())
        f = intfrc.install(m, {})
        _guest_or(m, 1 << 12)
        self.assertFalse(f.service(0))
        self.assertIsNone(f.step(0))

    def test_install_sees_a_force_already_set(self):
        m = _machine()
        m.uc.mem_write(INTFRCH0, struct.pack('>I', 1 << 12))
        f = intfrc.install(m, {})
        self.assertEqual(f.asserted, {44})

    def test_checkpoint_round_trip(self):
        m = _machine()
        f = intfrc.install(m, {})
        _guest_or(m, 1 << 12)
        f.service(0)
        g = intfrc.install(_machine(), {})
        g.restore_checkpoint_state(f.checkpoint_state())
        self.assertEqual(g.checkpoint_state(), f.checkpoint_state())


if __name__ == '__main__':
    unittest.main()
