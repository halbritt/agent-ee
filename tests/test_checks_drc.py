"""Native DRC shares the exact disposition contract, including parity/connectivity."""
import unittest
from tests.test_checks_erc import NativeWaiverContract


class NativeDrcWaiverTests(NativeWaiverContract, unittest.TestCase):
    kind = 'drc'
