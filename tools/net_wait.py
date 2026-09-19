#!/usr/bin/env python3
"""Wait for outbound network before a timer job touches the network.

The worker timers fire on a fixed cadence, including in the seconds right after
the box wakes from sleep, before routing (and Tailscale) is back up. The first
API call then dies with `Network is unreachable` or `Temporary failure in name
resolution`, the unit fails, and the OnFailure notifier fires a false alarm.

`wait_for_network()` probes reachability a few times with a short backoff and
returns True as soon as a TCP connection succeeds. A job calls it first and, if
it returns False, exits cleanly (skip this tick) instead of crashing, so a wake
transient no longer produces a failed unit or a spurious Telegram alert.
"""
import socket
import time

# Connection-only probes. The two IP literals need no DNS, so they answer even
# when only routing is up; the hostname probe is last so a resolver-only outage
# (gaierror) is caught too. A refused/unreachable host returns fast, so a fully
# down network does not sit on the timeout.
_PROBES = (("1.1.1.1", 443), ("8.8.8.8", 443), ("api.telegram.org", 443))


def network_up(timeout=3.0):
    """True if any probe accepts a TCP connection within `timeout` seconds."""
    for host, port in _PROBES:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def wait_for_network(attempts=6, delay=5.0, timeout=3.0):
    """Poll network_up() up to `attempts` times, sleeping `delay` between tries.

    Returns True once reachable, False if still down after the last attempt.
    """
    for i in range(attempts):
        if network_up(timeout=timeout):
            return True
        if i < attempts - 1:
            time.sleep(delay)
    return False


if __name__ == "__main__":
    import sys
    if "--wait" in sys.argv[1:]:
        # Gate for shell timer jobs: exit 0 once the network is up (retrying
        # briefly), non-zero if it is still down so the caller can skip the tick.
        sys.exit(0 if wait_for_network() else 1)
    print("network up" if network_up() else "network down")
    sys.exit(0)
