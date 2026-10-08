"""The worker's systemd units, checked as files.

The units in .agents/systemd are copied onto the always-on worker by
.agents/systemd/install.sh, which rewrites the vault path to the checkout it
runs from. That rewrite only works if every unit spells the vault one way, so a
unit that says ~/brainless or /home/<user>/projects/brainless would silently
keep pointing at the wrong tree. A timer without Persistent=true skips the
calendar runs it missed while the worker was off. A timer whose service was
renamed starts nothing. None of these fail loudly on the worker; they fail
here instead.

Stdlib only: each unit is read with configparser (case-sensitive keys, no
interpolation, duplicate keys tolerated), plus a raw line scan for the keys
that may repeat (ExecStart*).
"""
from pathlib import Path
import configparser
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
UNITS = ROOT / ".agents" / "systemd"
INSTALL = UNITS / "install.sh"
OWNER_ONLY = UNITS / "owner-only.txt"

VAULT_SPELLING = "%h/projects/brainless"

# Timers that deliberately do not catch up a missed run. Each says
# Persistent=false in the file with a comment giving the reason.
PERSISTENT_OPT_OUT = {
    "brainless-dialectic-trigger.timer",   # minute poller, nothing to catch up
    "brainless-dreaming.timer",            # night-only run, never during the day
}

# A path token that names the vault: anything ending in "brainless" right
# before a slash, a quote, whitespace or the end. The git lock
# (%h/.brainless-git.lock) and ~/.config/brainless/<file> do not match
# because "brainless" is followed by "-" or by a further path segment that is
# not engine code.
VAULT_TOKEN = re.compile(r"""[^\s'"=]*brainless(?=/(?:\.agents|tools)/|['"\s]|$)""")


def units(suffix):
    return sorted(UNITS.glob(f"*{suffix}"))


def parse(path):
    cp = configparser.ConfigParser(strict=False, allow_no_value=True,
                                   interpolation=None)
    cp.optionxform = str  # systemd keys are case-sensitive
    cp.read(path, encoding="utf-8")
    return cp


def raw_values(path, key_prefixes):
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip().startswith(key_prefixes):
            out.append((key.strip(), value.strip()))
    return out


def owner_only_names():
    names = []
    for line in OWNER_ONLY.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.append(line)
    return names


class UnitFilesParse(unittest.TestCase):
    def test_every_unit_parses(self):
        all_units = units(".service") + units(".timer")
        self.assertTrue(all_units, "no units found under .agents/systemd")
        for path in all_units:
            with self.subTest(unit=path.name):
                cp = parse(path)
                self.assertTrue(cp.has_section("Unit"), "missing [Unit]")


class VaultSpelling(unittest.TestCase):
    def test_exec_and_workdir_use_the_one_vault_spelling(self):
        for path in units(".service"):
            for key, value in raw_values(path, ("ExecStart", "ExecStop", "WorkingDirectory")):
                for token in VAULT_TOKEN.findall(value):
                    with self.subTest(unit=path.name, key=key):
                        self.assertEqual(
                            token, VAULT_SPELLING,
                            f"{key} names the vault as {token!r}; use {VAULT_SPELLING} "
                            "(install.sh rewrites it to the real checkout)")

    def test_engine_paths_sit_under_the_vault(self):
        # Any .agents/ or tools/ path in an ExecStart line must hang off the vault.
        engine = re.compile(r"""([^\s'"]*)/(?:\.agents|tools)/""")
        for path in units(".service"):
            for key, value in raw_values(path, ("ExecStart",)):
                for prefix in engine.findall(value):
                    with self.subTest(unit=path.name, key=key):
                        self.assertEqual(prefix, VAULT_SPELLING)

    def test_installer_rewrites_the_same_spelling(self):
        self.assertIn(VAULT_SPELLING, INSTALL.read_text(encoding="utf-8"))


class Timers(unittest.TestCase):
    def test_every_timer_is_persistent(self):
        for path in units(".timer"):
            with self.subTest(timer=path.name):
                cp = parse(path)
                self.assertTrue(cp.has_section("Timer"), "missing [Timer]")
                value = cp.get("Timer", "Persistent", fallback=None)
                if path.name in PERSISTENT_OPT_OUT:
                    self.assertEqual(value, "false",
                                     "listed as an opt-out, so it must say Persistent=false")
                else:
                    self.assertEqual(value, "true", "add Persistent=true")

    def test_every_timer_has_a_schedule(self):
        for path in units(".timer"):
            with self.subTest(timer=path.name):
                timer = parse(path)["Timer"]
                self.assertTrue(
                    "OnCalendar" in timer or "OnBootSec" in timer or "OnUnitActiveSec" in timer,
                    "needs OnCalendar, or OnBootSec/OnUnitActiveSec")

    def test_every_timer_targets_an_existing_service(self):
        for path in units(".timer"):
            with self.subTest(timer=path.name):
                target = parse(path)["Timer"].get("Unit") or path.stem + ".service"
                self.assertTrue((UNITS / target).is_file(), f"{target} does not exist")

    def test_opt_outs_exist(self):
        for name in PERSISTENT_OPT_OUT:
            self.assertTrue((UNITS / name).is_file(), f"stale opt-out: {name}")


class Installer(unittest.TestCase):
    def setUp(self):
        self.text = INSTALL.read_text(encoding="utf-8")

    def test_self_link_guard_kept(self):
        # A unit installed with `systemctl --user link` is the repo file; copying
        # onto it fails, and under set -e that stops the install.
        self.assertRegex(self.text, r'\[ "\$f" -ef "[^"]*\$f" \]')

    def test_all_flag_present_and_documented(self):
        self.assertIn("--all)", self.text, "option parser lacks --all")
        header = self.text.split("\nset ", 1)[0]
        self.assertIn("--all", header, "--all is not documented in the header comment")

    def test_owner_only_units_exist(self):
        names = owner_only_names()
        self.assertTrue(names, "owner-only.txt is empty")
        for name in names:
            with self.subTest(unit=name):
                self.assertTrue((UNITS / name).is_file(), f"{name} does not exist")


if __name__ == "__main__":
    unittest.main()
