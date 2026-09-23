"""Every symbol emu/symbols.py needs should resolve, and the alternatives
inside an AnyOf should agree -- checked on whatever images are extracted,
rather than discovered one hang at a time.

Both failures are silent at runtime: a symbol that resolves to None makes its
caller skip whatever it was going to do, and a `Fixed` fallback that resolves
to a different address than the rule that won will answer, wrongly, on the
first build where that rule stops matching.

The checks live in tools/symaudit.py so the test and the command-line report
cannot drift apart.
"""

import importlib.util
import unittest
from pathlib import Path

MAIN = Path("sections/section_3_MAIN_OS.bin")
TOOL = Path("tools/symaudit.py")


def _load_tool():
    spec = importlib.util.spec_from_file_location("symaudit", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(MAIN.exists() and TOOL.exists(), "MAIN OS image absent")
class SymbolAuditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = _load_tool()
        cls.findings = cls.tool.analyse(MAIN.read_bytes())

    def test_no_unexpected_unresolved_symbols(self):
        """Anything unresolved must be on the tool's ALLOWED_NONE list, with
        a reason recorded there for why its absence is harmless."""
        self.assertEqual(
            self.findings["unexpected"], [],
            "symbol(s) resolved to None that nothing has justified:\n%s"
            % self.findings["profile"].report())

    def test_anyof_alternatives_agree(self):
        """Two alternatives that both resolve must resolve to the same
        address. Disagreement means the losing one is a wrong answer waiting
        for the winner to stop matching."""
        names = [name for name, _results in self.findings["disagreements"]]
        self.assertEqual(
            names, [],
            "AnyOf alternatives disagree for: %s -- run "
            "`python tools/symaudit.py` for the addresses" % names)

    def test_allowlist_entries_are_still_unresolved(self):
        """An allowlist that outlives the problem it documents stops meaning
        anything. If a name on it resolves now, the entry should go."""
        stale = sorted(n for n in self.tool.ALLOWED_NONE
                       if n not in self.findings["unresolved"])
        self.assertEqual(
            stale, [],
            "ALLOWED_NONE names that resolve now and should be removed: %s"
            % stale)


if __name__ == "__main__":
    unittest.main()
