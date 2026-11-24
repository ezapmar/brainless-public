"""net_wait.network_up: routing alone is not a network.

What must hold: a TCP probe that connects is not enough while no name
resolves, and a dead route is reported without asking the resolver.

Run: python3 -B -m unittest tools.tests.test_net_wait -v
"""
import unittest
from unittest import mock

import net_wait


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class NetworkUp(unittest.TestCase):
    def test_route_without_dns_is_down(self):
        with mock.patch.object(net_wait.socket, "create_connection", return_value=_Conn()), \
             mock.patch.object(net_wait.socket, "getaddrinfo", side_effect=OSError("no resolver")):
            self.assertFalse(net_wait.network_up())

    def test_route_and_one_resolving_name_is_up(self):
        answers = [OSError("first name fails"), [("ok",)]]
        with mock.patch.object(net_wait.socket, "create_connection", return_value=_Conn()), \
             mock.patch.object(net_wait.socket, "getaddrinfo", side_effect=answers):
            self.assertTrue(net_wait.network_up())

    def test_no_route_does_not_ask_the_resolver(self):
        with mock.patch.object(net_wait.socket, "create_connection", side_effect=OSError("unreachable")), \
             mock.patch.object(net_wait.socket, "getaddrinfo") as resolver:
            self.assertFalse(net_wait.network_up())
            resolver.assert_not_called()


if __name__ == "__main__":
    unittest.main()
