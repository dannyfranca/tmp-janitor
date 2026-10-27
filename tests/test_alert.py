import json
import shlex
import sys
import unittest

from support import JanitorTestCase


class AlertTests(JanitorTestCase):
    def setUp(self):
        super().setUp()
        commands = self.root / "commands"
        commands.mkdir()
        self.log = self.root / "notify.jsonl"
        notify = commands / "notify-send"
        notify.write_text(
            f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -c '"
            "import json, os, sys\n"
            "open(os.environ[\"NOTIFY_LOG\"], \"a\").write(json.dumps(sys.argv[1:]) + \"\\n\")\n"
            "print(\"42\")\n"
            "sys.exit(int(os.environ.get(\"NOTIFY_STATUS\", \"0\")))\n"
            "' \"$@\"\n"
        )
        notify.chmod(0o755)
        self.env.update(PATH=f"{commands}:{self.env['PATH']}", NOTIFY_LOG=str(self.log))
        self.watched = self.root / "watched"
        self.watched.mkdir()
        (self.watched / "big").write_bytes(b"x" * 8192)

    def use_percent(self, percent):
        self.write_config(
            f"""
[sweep]
schedule = "daily"

[[sweep.dirs]]
path = "{self.watched}"
idle = "1d"

[alert]
schedule = "*:0/10"
path = "{self.watched}"
percent = {percent}
"""
        )

    def notifications(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_notifies_above_the_threshold(self):
        self.use_percent(0.0001)
        result = self.run_janitor("alert")
        self.assertIn("% full", result.stdout)
        [arguments] = self.notifications()
        self.assertIn("--urgency=critical", arguments)
        self.assertNotIn("--replace-id", arguments)
        self.assertIn("big (", arguments[-1])

    def test_updates_the_same_notification(self):
        self.use_percent(0.0001)
        self.run_janitor("alert")
        self.run_janitor("alert")
        second = self.notifications()[1]
        self.assertEqual(second[second.index("--replace-id") + 1], "42")

    def test_stays_quiet_below_the_threshold(self):
        self.use_percent(100)
        result = self.run_janitor("alert")
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.notifications(), [])

    def test_fails_when_the_notification_fails(self):
        self.use_percent(0.0001)
        self.env["NOTIFY_STATUS"] = "1"
        result = self.run_janitor("alert", expected_status=1)
        self.assertIn("could not send notification", result.stderr)

    def test_succeeds_when_the_notification_id_cannot_be_saved(self):
        self.use_percent(0.0001)
        self.env["XDG_STATE_HOME"] = str(self.watched / "big")
        result = self.run_janitor("alert")
        self.assertIn("could not save notification id", result.stderr)
        self.assertEqual(len(self.notifications()), 1)

    def test_needs_an_alert_section(self):
        self.write_config(
            f'[sweep]\nschedule = "daily"\n[[sweep.dirs]]\npath = "{self.watched}"\nidle = "1d"\n'
        )
        self.run_janitor("alert", expected_status=2)


if __name__ == "__main__":
    unittest.main()
