# pyright: reportMissingImports=false
"""patches/unicorn-2.1.4-m68k-fast-mem.patch keeps hooks and SMC exact.

The patch stops Unicorn forcing EVERY guest load and store through its slow
C helper whenever any memory hook exists, and lets written pages that hold
no translated code back onto the fast path. It is only a speed-up if nothing
observable changes, so these pin what must not:

  * a memory hook fires for accesses to its page, and not for other pages;
  * a hook added AFTER the code ran (TLB already filled) still fires;
  * a deleted hook stops firing;
  * self-modifying code is still caught: rewriting an instruction that has
    already executed runs the new instruction;
  * a store to a page that holds code (and is therefore kept on the checked
    path) still lands.
"""

import struct
import unittest

from unicorn import (UC_ARCH_M68K, UC_HOOK_MEM_INVALID, UC_HOOK_MEM_READ,
                     UC_HOOK_MEM_WRITE, UC_MODE_BIG_ENDIAN, Uc)
from unicorn import m68k_const

CODE = 0x10000
DATA = 0x40000000
OTHER = 0x40100000


def reg(name):
    return getattr(m68k_const, "UC_M68K_REG_" + name)


def machine(code_words):
    uc = Uc(UC_ARCH_M68K, UC_MODE_BIG_ENDIAN)
    uc.ctl_set_cpu_model(m68k_const.UC_CPU_M68K_CFV4E)
    uc.mem_map(CODE, 0x10000)
    uc.mem_map(DATA, 0x200000)
    code = b"".join(struct.pack(">H", int(w, 16)) for w in code_words)
    uc.mem_write(CODE, code)
    uc.reg_write(reg("SR"), 0x2700)
    return uc, CODE + len(code)


# move.l d0,(a0) ; move.l (a1),d1 ; move.l d0,(a2) ; move.l (a3),d2
ACCESS = ["2080", "2211", "2480", "2413"]


def run_access(uc, end):
    uc.reg_write(reg("D0"), 0x11223344)
    uc.reg_write(reg("A0"), DATA + 0x10)
    uc.reg_write(reg("A1"), DATA + 0x10)
    uc.reg_write(reg("A2"), OTHER + 0x20)
    uc.reg_write(reg("A3"), OTHER + 0x20)
    uc.emu_start(CODE, end)


class HookRoutingTest(unittest.TestCase):
    def test_hooks_fire_only_for_their_page(self):
        uc, end = machine(ACCESS)
        seen = []
        uc.hook_add(UC_HOOK_MEM_WRITE, lambda u, t, a, s, v, d: seen.append(
            ("w", a, v)), begin=DATA, end=DATA + 0xFFF)
        uc.hook_add(UC_HOOK_MEM_READ, lambda u, t, a, s, v, d: seen.append(
            ("r", a)), begin=DATA, end=DATA + 0xFFF)
        run_access(uc, end)
        self.assertEqual(seen, [("w", DATA + 0x10, 0x11223344),
                                ("r", DATA + 0x10)])
        self.assertEqual(uc.reg_read(reg("D1")), 0x11223344)
        self.assertEqual(uc.reg_read(reg("D2")), 0x11223344)

    def test_a_hook_added_after_the_code_ran_still_fires(self):
        uc, end = machine(ACCESS)
        run_access(uc, end)               # code translated, TLB filled
        seen = []
        uc.hook_add(UC_HOOK_MEM_WRITE, lambda u, t, a, s, v, d: seen.append(a),
                    begin=OTHER, end=OTHER + 0xFFF)
        run_access(uc, end)
        self.assertEqual(seen, [OTHER + 0x20])

    def test_a_deleted_hook_stops_firing(self):
        uc, end = machine(ACCESS)
        seen = []
        h = uc.hook_add(UC_HOOK_MEM_WRITE, lambda u, t, a, s, v, d:
                        seen.append(a), begin=DATA, end=DATA + 0xFFF)
        run_access(uc, end)
        uc.hook_del(h)
        run_access(uc, end)
        self.assertEqual(seen, [DATA + 0x10])

    def test_a_global_invalid_memory_hook_changes_nothing(self):
        # emu.harness.Machine maps pages on demand from a global
        # UC_HOOK_MEM_INVALID hook. It must not stop write hooks on other
        # pages firing, nor keep SMC detection from working -- and (the
        # reason for this test) it must not pin every store to the slow path.
        uc, end = machine(ACCESS)
        uc.hook_add(UC_HOOK_MEM_INVALID, lambda *a: False)
        seen = []
        uc.hook_add(UC_HOOK_MEM_WRITE, lambda u, t, a, s, v, d: seen.append(a),
                    begin=OTHER, end=OTHER + 0xFFF)
        run_access(uc, end)
        run_access(uc, end)
        self.assertEqual(seen, [OTHER + 0x20, OTHER + 0x20])

    def test_a_whole_address_space_hook_sees_everything(self):
        uc, end = machine(ACCESS)
        seen = []
        uc.hook_add(UC_HOOK_MEM_WRITE, lambda u, t, a, s, v, d: seen.append(a))
        run_access(uc, end)
        self.assertEqual(seen, [DATA + 0x10, OTHER + 0x20])


class SelfModifyingCodeTest(unittest.TestCase):
    def test_rewriting_translated_code_runs_the_new_code(self):
        # CODE+0:    move.w d4,(a4)        rewrite the target's instruction
        # CODE+2:    bra.w  CODE+0x40
        # CODE+0x40: moveq #2,d5           (a separate, already-translated TB)
        # The target runs once first, so its block is translated and its page
        # re-protected; the guest store then has to invalidate it, within the
        # same emu_start. (Rewriting a later instruction of the block that is
        # EXECUTING is not caught on m68k with or without the patch: QEMU has
        # no precise SMC for it, and neither does a ColdFire without a cache
        # flush.)
        uc, _ = machine(["3884", "6000", "003c"])
        uc.mem_write(CODE + 0x40, bytes.fromhex("7a02"))
        uc.emu_start(CODE + 0x40, CODE + 0x42)
        self.assertEqual(uc.reg_read(reg("D5")), 2)
        uc.reg_write(reg("D4"), 0x7A07)   # moveq #7,d5
        uc.reg_write(reg("A4"), CODE + 0x40)
        uc.emu_start(CODE, CODE + 0x42)
        self.assertEqual(uc.reg_read(reg("D5")), 7)

    def test_rewriting_code_between_runs_is_seen(self):
        uc, end = machine(["7a02"])       # moveq #2,d5
        uc.emu_start(CODE, end)
        self.assertEqual(uc.reg_read(reg("D5")), 2)
        # A guest store (not uc.mem_write) rewrites it, then it runs again.
        uc2_code = ["3884"]               # move.w d4,(a4)
        uc.mem_write(CODE + 0x100, struct.pack(">H", int(uc2_code[0], 16)))
        uc.reg_write(reg("D4"), 0x7A09)   # moveq #9,d5
        uc.reg_write(reg("A4"), CODE)
        uc.emu_start(CODE + 0x100, CODE + 0x102)
        uc.emu_start(CODE, end)
        self.assertEqual(uc.reg_read(reg("D5")), 9)

    def test_stores_next_to_code_land(self):
        # A data word in the same page as the code, written in a loop.
        # moveq #3,d0 ; loop: move.l d0,(a0) ; subq.l #1,d0 ; bne.s loop
        uc, end = machine(["7003", "2080", "5380", "66fa"])
        uc.reg_write(reg("A0"), CODE + 0x800)
        uc.emu_start(CODE, end)
        self.assertEqual(struct.unpack(">I", uc.mem_read(CODE + 0x800, 4))[0],
                         1)


if __name__ == "__main__":
    unittest.main()
