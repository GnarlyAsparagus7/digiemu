import os
import sys
import unittest
from collections import Counter
from unittest import mock


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, 'tools')
sys.path.insert(0, TOOLS)

import bootcheck
import panelsweep


class BootcheckGateTest(unittest.TestCase):
    def test_verify_fails_when_arm_digests_differ(self):
        first = {'top_blocks': [], 'uart_tail': ''}
        second = {'top_blocks': [], 'uart_tail': 'different'}
        argv = ['bootcheck.py', '--syx', 'fw.syx', '--snapshot', 'boot.snap',
                '--verify']
        with mock.patch.object(sys, 'argv', argv), \
             mock.patch.object(bootcheck.config, 'main_image', return_value='unused'), \
             mock.patch('builtins.open', mock.mock_open(read_data=b'')), \
             mock.patch.object(bootcheck.symbols, 'resolve', return_value=object()), \
             mock.patch.object(bootcheck, 'run_arm', side_effect=[first, second]), \
             mock.patch.object(bootcheck, 'classify', return_value=('MAIN_OS_RUNNING', [])), \
             mock.patch.object(bootcheck, 'digest', side_effect=lambda arm: 'first' if arm is first else 'second'), \
             mock.patch('builtins.print'):
            self.assertEqual(bootcheck.main(), 1)


class PanelsweepLimitTest(unittest.TestCase):
    def test_intro_wait_fails_at_the_instruction_cap(self):
        mark = Counter()
        with mock.patch.object(panelsweep, 'spin',
                               side_effect=[(5, 5, 'limit'), (5, 5, 'limit')]):
            pc, ok, stop = panelsweep.wait_for_handover(
                object(), 0, object(), mark, 10)
        self.assertEqual((pc, ok, stop), (5, False, 'cap'))


if __name__ == '__main__':
    unittest.main()
