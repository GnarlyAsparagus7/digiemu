"""emu/symbols.py keeps signatures as digests (`H`), never firmware bytes.

The digest must select exactly the positions the literal bytes would, and the
symbol table must not quietly grow a literal again: those bytes are Elektron's.
"""
import ast
import os
import random
import unittest

from emu import symbols as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def image_with(sig, at, size=4096, seed=1):
    rnd = random.Random(seed)
    img = bytearray(rnd.randrange(256) for _ in range(size))
    for p in at:
        img[p:p + len(sig)] = sig
    return bytes(img)


class HashedSignatureTest(unittest.TestCase):
    # Made-up bytes, not a firmware signature (that would defeat the point).
    raw = random.Random(7).randbytes(24)

    def test_finds_exactly_where_the_bytes_are(self):
        h = S.H.of(self.raw)
        img = image_with(self.raw, (100, 2000))
        self.assertEqual(h.positions(img), [100, 2000])
        self.assertTrue(h.matches_at(img, 100))
        self.assertFalse(h.matches_at(img, 101))

    def test_wildcards_may_differ(self):
        h = S.H.of(self.raw, wild={8, 9, 10, 11})
        other = bytearray(self.raw)
        other[8:12] = b'\xde\xad\xbe\xef'
        self.assertEqual(h.positions(image_with(bytes(other), (300,))), [300])
        other[0] ^= 1                              # a fixed byte differs
        self.assertEqual(h.positions(image_with(bytes(other), (300,))), [])

    def test_literal_and_digest_rules_agree(self):
        """A Sig made from hex and one made from the H it prints."""
        hexs = self.raw.hex()
        img = image_with(self.raw, (512,))
        a = S.Sig(hexs, hi=S.DATA_HI)
        b = S.Sig(eval(repr(a.pat), vars(S)), hi=S.DATA_HI)
        self.assertEqual(a.resolve(img, S.LOAD_ADDR, {}), b.resolve(img, S.LOAD_ADDR, {}))
        self.assertEqual(a.resolve(img, S.LOAD_ADDR, {})[0], S.LOAD_ADDR + 512)

    def test_wild_must_already_be_wildcarded(self):
        h = S.H.of(self.raw)
        with self.assertRaises(ValueError):
            S.Sig(h, wild=(3,))

    def test_fixed_verifies_against_a_digest(self):
        img = image_with(self.raw, (64,))
        good = S.Fixed(S.LOAD_ADDR + 64, verify=S.H.of(self.raw[:8]))
        bad = S.Fixed(S.LOAD_ADDR + 65, verify=S.H.of(self.raw[:8]))
        self.assertEqual(good.resolve(img, S.LOAD_ADDR, {})[0], S.LOAD_ADDR + 64)
        self.assertIsNone(bad.resolve(img, S.LOAD_ADDR, {})[0])

    def test_short_anchor_when_wildcards_leave_no_four_bytes(self):
        raw = bytes(range(1, 13))
        h = S.H.of(raw, wild={3, 4, 5, 9, 10, 11})    # fixed runs of 3
        self.assertEqual(len(h.anchor), 3)
        self.assertEqual(h.positions(image_with(raw, (40,))), [40])

    def test_the_symbol_table_carries_no_literal_bytes(self):
        with open(os.path.join(ROOT, 'emu', 'symbols.py'), encoding='utf-8') as fh:
            tree = ast.parse(fh.read())
        literal = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            args = []
            if node.func.id in ('Sig', 'SigWhere', 'SigAt', 'Opcode') and node.args:
                args.append(node.args[0])
            if node.func.id == 'Fixed':
                args += [k.value for k in node.keywords if k.arg == 'verify']
            for a in args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    literal.append('%s at line %d' % (node.func.id, node.lineno))
        self.assertEqual(literal, [], 'use H(...) (tools/mksig.py prints it)')


if __name__ == '__main__':
    unittest.main()
