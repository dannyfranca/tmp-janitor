import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tmp-janitor-install-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.commands = self.root / "commands"
        self.commands.mkdir()
        self.log = self.root / "commands.jsonl"
        stub = self.commands / "stub"
        stub.write_text(
            f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(self.commands / 'stub.py'))} \"$0\" \"$@\"\n"
        )
        stub.chmod(0o755)
        (self.commands / "stub.py").write_text(
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "command = Path(sys.argv[1]).name\n"
            "args = sys.argv[2:]\n"
            "with open(os.environ['COMMAND_LOG'], 'a') as log:\n"
            "    log.write(json.dumps([command, *args]) + '\\n')\n"
            "if command == 'systemd-analyze' and args[-1] == os.environ.get('INVALID_CALENDAR'):\n"
            "    sys.exit(1)\n"
        )
        for command in ("systemctl", "systemd-analyze"):
            (self.commands / command).symlink_to(stub)
        self.home = self.root / "home"
        self.config_home = self.root / "config"
        self.env = os.environ.copy()
        self.env.pop("TMP_JANITOR_CONFIG", None)
        self.env.update(
            HOME=str(self.home),
            PATH=f"{self.commands}:{self.env['PATH']}",
            XDG_CONFIG_HOME=str(self.config_home),
            XDG_STATE_HOME=str(self.root / "state"),
            COMMAND_LOG=str(self.log),
        )
        self.units = self.config_home / "systemd/user"
        self.config = self.config_home / "tmp-janitor/config.toml"

    def run_script(self, *args, expected_status=0):
        result = subprocess.run(
            [str(REPO / args[0]), *args[1:]], env=self.env, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, expected_status, result.stdout + result.stderr)
        self.stderr = result.stderr
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def write_config(self, sweep_schedule, alert=True):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        text = f'[sweep]\nschedule = {sweep_schedule}\n[[sweep.dirs]]\npath = "/tmp"\nidle = "1d"\n'
        if alert:
            text += '[alert]\nschedule = "hourly"\npath = "/tmp"\npercent = 80\n'
        self.config.write_text(text)

    def test_installs_the_example_config_and_both_timers(self):
        commands = self.run_script("install.sh")
        self.assertEqual(self.config.read_text(), (REPO / "config.example.toml").read_text())
        self.assertTrue(os.access(self.home / ".local/bin/tmp-janitor", os.X_OK))
        self.assertIn("OnCalendar=daily\n", (self.units / "tmp-janitor-sweep.timer").read_text())
        self.assertIn("OnCalendar=*:0/10\n", (self.units / "tmp-janitor-alert.timer").read_text())
        self.assertTrue((self.units / "tmp-janitor-alert.service").exists())
        self.assertIn(
            ["systemctl", "--user", "enable", "tmp-janitor-sweep.timer", "tmp-janitor-alert.timer"], commands
        )
        self.assertIn(
            ["systemctl", "--user", "restart", "tmp-janitor-sweep.timer", "tmp-janitor-alert.timer"], commands
        )

    def test_writes_one_line_for_each_schedule(self):
        self.write_config('["06:00", "Mon *-*-* 18:00"]')
        self.run_script("install.sh")
        timer = (self.units / "tmp-janitor-sweep.timer").read_text()
        self.assertIn("OnCalendar=06:00\nOnCalendar=Mon *-*-* 18:00\n", timer)
        self.assertIn("OnCalendar=hourly\n", (self.units / "tmp-janitor-alert.timer").read_text())

    def test_keeps_an_existing_config(self):
        self.write_config('"weekly"')
        before = self.config.read_text()
        self.run_script("install.sh")
        self.assertEqual(self.config.read_text(), before)

    def test_removes_the_alert_timer_without_an_alert_section(self):
        self.run_script("install.sh")
        self.write_config('"daily"', alert=False)
        commands = self.run_script("install.sh")
        self.assertFalse((self.units / "tmp-janitor-alert.timer").exists())
        self.assertFalse((self.units / "tmp-janitor-alert.service").exists())
        self.assertIn(["systemctl", "--user", "disable", "--now", "tmp-janitor-alert.timer"], commands)
        self.assertIn(["systemctl", "--user", "enable", "tmp-janitor-sweep.timer"], commands)

    def test_invalid_schedule_stops_before_changes(self):
        self.write_config('"not a time"')
        self.env["INVALID_CALENDAR"] = "not a time"
        commands = self.run_script("install.sh", expected_status=1)
        self.assertIn("invalid sweep schedule", self.stderr)
        self.assertFalse((self.home / ".local/bin/tmp-janitor").exists())
        self.assertFalse(self.units.exists())
        self.assertNotIn("systemctl", [command[0] for command in commands])

    def test_invalid_config_stops_before_changes(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text("[sweep]\nschedule = 1\n")
        self.run_script("install.sh", expected_status=1)
        self.assertIn("could not load config", self.stderr)
        self.assertFalse(self.units.exists())

    def test_uninstall_keeps_the_config(self):
        self.run_script("install.sh")
        commands = self.run_script("uninstall.sh")
        self.assertFalse((self.home / ".local/bin/tmp-janitor").exists())
        self.assertEqual(list(self.units.iterdir()), [])
        self.assertTrue(self.config.exists())
        self.assertIn(["systemctl", "--user", "disable", "--now", "tmp-janitor-sweep.timer"], commands)
        self.assertEqual(commands[-1], ["systemctl", "--user", "daemon-reload"])

    def test_uninstall_purge_removes_config_and_state(self):
        self.run_script("install.sh")
        state = self.root / "state/tmp-janitor"
        state.mkdir(parents=True)
        self.run_script("uninstall.sh", "--purge")
        self.assertFalse(self.config.parent.exists())
        self.assertFalse(state.exists())

    @unittest.skipUnless(shutil.which("systemd-analyze", path=os.defpath + ":/usr/bin"), "needs systemd-analyze")
    def test_installed_units_pass_systemd_verify(self):
        self.run_script("install.sh")
        for name in ("tmp-janitor-sweep", "tmp-janitor-alert"):
            service = self.units / f"{name}.service"
            service.write_text(service.read_text().replace("%h/.local/bin/tmp-janitor", "/usr/bin/true"))
        result = subprocess.run(
            ["/usr/bin/systemd-analyze", "--user", "verify", *map(str, sorted(self.units.iterdir()))],
            env=self.env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
