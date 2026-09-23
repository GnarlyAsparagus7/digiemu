# pyright: reportMissingImports=false
"""patches/unicorn-2.1.4-m68k-digikit-speed.patch: each engine option against
the behaviour without it.

The options are speed-ups the emulator turns on (emu/native.py), so each one
is pinned to what it may and may not change:

  * NATIVE_RTE: `rte` restores the same PC, SR and A7 as the Python frame pop
    in Machine.install_exceptions, without reaching UC_HOOK_INTR; every other
    exception still does;
  * NO_MEM_EXIT: memory hooks fire as before and a faulting access still
    stops at the faulting instruction; only a memory hook that calls
    emu_stop now stops at the end of its block;
  * NO_HOOK_PC_SYNC: memory hooks fire with the same addresses and values,
    and only the PC they would read is no longer the accessing instruction's.

The fused MAC helper has no option; tests/test_unicorn_emac.py runs through
it.
"""

import struct
import unittest

from unicorn import (UC_ARCH_M68K, UC_HOOK_INTR, UC_HOOK_MEM_READ,
                     UC_HOOK_MEM_WRITE, UC_MODE_BIG_ENDIAN, UC_PROT_READ, Uc,
                     UcError)
from unicorn import m68k_const

from emu import native

CODE = 0x10000
DATA = 0x40000000
READONLY = 0x60000000
UNMAPPED = 0x50000000
EXCP_RTE = 0x100


def reg(name):
    return getattr(m68k_const, "UC_M68K_REG_" + name)


def machine(code_words, options=0):
    uc = Uc(UC_ARCH_M68K, UC_MODE_BIG_ENDIAN)
    uc.ctl_set_cpu_model(m68k_const.UC_CPU_M68K_CFV4E)
    uc.mem_map(CODE, 0x10000)
    uc.mem_map(DATA, 0x10000)
    uc.mem_map(READONLY, 0x1000, UC_PROT_READ)
    code = b"".join(struct.pack(">H", int(w, 16)) for w in code_words)
    uc.mem_write(CODE, code)
    uc.reg_write(reg("SR"), 0x2700)
    if options:
        native.enable_options(uc, options)
    return uc, CODE + len(code)


class OptionsApiTest(unittest.TestCase):
    def test_the_library_has_all_three(self):
        self.assertEqual(native.options_supported(),
                         native.NATIVE_RTE | native.NO_MEM_EXIT
                         | native.NO_HOOK_PC_SYNC)

    def test_options_accumulate(self):
        uc, _ = machine(["4e71"])
        self.assertEqual(native.enable_options(uc, native.NO_MEM_EXIT),
                         native.NO_MEM_EXIT)
        self.assertEqual(native.enable_options(uc, native.NATIVE_RTE),
                         native.NO_MEM_EXIT | native.NATIVE_RTE)

    def test_an_unknown_bit_is_refused(self):
        uc, _ = machine(["4e71"])
        self.assertNotEqual(native._set_options(native._handle(uc), 8), 0)


class NativeRteTest(unittest.TestCase):
    def run_rte(self, sr, options):
        # rte, with a format-4 frame (vector 64) returning to CODE+0x100.
        uc, _ = machine(["4e73"], options)
        uc.mem_write(DATA + 0x100, struct.pack(
            ">HHI", 0x4000 | (64 << 2), sr, CODE + 0x100))
        uc.reg_write(reg("A7"), DATA + 0x100)
        seen = []

        def on_intr(u, vec, data):
            # Machine.install_exceptions' pop, for the run without the option.
            seen.append(vec)
            if vec == EXCP_RTE:
                sp = u.reg_read(reg("A7"))
                _fmt, s, pc = struct.unpack(">HHI", u.mem_read(sp, 8))
                u.reg_write(reg("SR"), s)
                u.reg_write(reg("PC"), pc)
                u.reg_write(reg("A7"), sp + 8)
        uc.hook_add(UC_HOOK_INTR, on_intr)
        uc.emu_start(CODE, CODE + 0x100)
        return (uc.reg_read(reg("PC")), uc.reg_read(reg("SR")),
                uc.reg_read(reg("A7"))), seen

    def test_native_rte_matches_the_python_pop(self):
        # 0x0004 returns to user mode: with one stack pointer (CACR.EUSP
        # clear) A7 must not switch.
        for sr in (0x2004, 0x0004, 0x2709):
            with self.subTest(sr=hex(sr)):
                python, seen = self.run_rte(sr, 0)
                self.assertEqual(seen, [EXCP_RTE])
                native_, seen = self.run_rte(sr, native.NATIVE_RTE)
                self.assertEqual(seen, [])
                self.assertEqual(native_, python)
                self.assertEqual(native_, (CODE + 0x100, sr, DATA + 0x108))

    def test_other_exceptions_still_reach_the_hook(self):
        uc, end = machine(["4e40"], native.NATIVE_RTE)       # trap #0
        seen = []
        uc.hook_add(UC_HOOK_INTR, lambda u, v, d: (seen.append(v), u.emu_stop()))
        uc.emu_start(CODE, end)
        self.assertEqual(seen, [32])


# moveq #1,d2 ; move.l d0,(a0) ; moveq #1,d3 ; addq.l #1,d4 ; move.l d0,(a1)
STORE_FAULT = ["7401", "2080", "7601", "5284", "2280"]
# The same with a load: move.l (a0),d0
LOAD_FAULT = ["7401", "2010", "7601", "5284", "2280"]


class NoMemExitTest(unittest.TestCase):
    def fault(self, words, target, options):
        uc, end = machine(words, options)
        uc.reg_write(reg("A0"), target)
        uc.reg_write(reg("A1"), DATA)
        uc.reg_write(reg("D0"), 0x77)
        with self.assertRaises(UcError) as caught:
            uc.emu_start(CODE, end)
        return (caught.exception.errno, uc.reg_read(reg("PC")),
                uc.reg_read(reg("D2")), uc.reg_read(reg("D3")),
                uc.reg_read(reg("D4")), bytes(uc.mem_read(DATA, 4)))

    def test_a_faulting_access_stops_at_that_instruction(self):
        # Stock Unicorn stops a faulting store with the exit check the option
        # removes; the patch leaves the block from the store helper instead.
        # Without that the rest of the block ran and its store landed.
        for name, words, target in (
                ("unmapped store", STORE_FAULT, UNMAPPED),
                ("read-only store", STORE_FAULT, READONLY),
                ("unmapped load", LOAD_FAULT, UNMAPPED)):
            with self.subTest(name):
                want = self.fault(words, target, 0)
                self.assertEqual(want[1:], (CODE + 2, 1, 0, 0, b"\0\0\0\0"))
                self.assertEqual(self.fault(words, target, native.NO_MEM_EXIT),
                                 want)

    def test_hooks_fire_and_values_land(self):
        # move.l d0,(a0) ; move.l (a1),d1
        uc, end = machine(["2080", "2211"], native.NO_MEM_EXIT)
        seen = []
        uc.hook_add(UC_HOOK_MEM_WRITE, lambda u, t, a, s, v, d: seen.append(
            ("w", a, v)))
        uc.hook_add(UC_HOOK_MEM_READ, lambda u, t, a, s, v, d: seen.append(
            ("r", a)))
        uc.reg_write(reg("D0"), 0x11223344)
        uc.reg_write(reg("A0"), DATA + 0x10)
        uc.reg_write(reg("A1"), DATA + 0x10)
        uc.emu_start(CODE, end)
        self.assertEqual(seen, [("w", DATA + 0x10, 0x11223344),
                                ("r", DATA + 0x10)])
        self.assertEqual(uc.reg_read(reg("D1")), 0x11223344)

    def test_a_hook_that_stops_now_stops_at_the_block_end(self):
        # move.l (a1),d1 ; moveq #1,d2 ; moveq #2,d3. Stock Unicorn abandons
        # the block at the access (back to its start, D1 not yet written);
        # with the option the block finishes. Changing the option flushes
        # translated code, so the same engine shows both.
        uc, end = machine(["2211", "7401", "7602"])
        uc.reg_write(reg("A1"), DATA)
        uc.mem_write(DATA, struct.pack(">I", 0x55))
        uc.hook_add(UC_HOOK_MEM_READ, lambda u, *a: u.emu_stop())
        uc.emu_start(CODE, end)
        self.assertEqual((uc.reg_read(reg("PC")), uc.reg_read(reg("D2"))),
                         (CODE, 0))
        native.enable_options(uc, native.NO_MEM_EXIT)
        uc.emu_start(CODE, end)
        self.assertEqual((uc.reg_read(reg("PC")), uc.reg_read(reg("D1")),
                          uc.reg_read(reg("D2")), uc.reg_read(reg("D3"))),
                         (end, 0x55, 1, 2))


class NoHookPcSyncTest(unittest.TestCase):
    # moveq #1,d2 ; move.l (a1),d1 ; moveq #3,d3 ; move.l d1,(a2)
    WORDS = ["7401", "2211", "7603", "2481"]

    def run_hooks(self, options):
        uc, end = machine(self.WORDS, options)
        uc.reg_write(reg("A1"), DATA)
        uc.reg_write(reg("A2"), DATA + 0x10)
        uc.mem_write(DATA, struct.pack(">I", 0x1234))
        seen = []
        uc.hook_add(UC_HOOK_MEM_READ, lambda u, t, a, s, v, d: seen.append(
            ("r", a, u.reg_read(reg("PC")))))
        uc.hook_add(UC_HOOK_MEM_WRITE, lambda u, t, a, s, v, d: seen.append(
            ("w", a, v, u.reg_read(reg("PC")))))
        uc.emu_start(CODE, end)
        return seen, bytes(uc.mem_read(DATA + 0x10, 4))

    def test_hooks_see_the_same_accesses(self):
        exact, stored = self.run_hooks(0)
        self.assertEqual(exact, [("r", DATA, CODE + 2),
                                 ("w", DATA + 0x10, 0x1234, CODE + 6)])
        loose, stored2 = self.run_hooks(native.NO_HOOK_PC_SYNC)
        self.assertEqual([e[:-1] for e in loose], [e[:-1] for e in exact])
        self.assertEqual(stored2, stored)


if __name__ == "__main__":
    unittest.main()
