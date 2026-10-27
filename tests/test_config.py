import unittest

from support import REPO, JanitorTestCase


SWEEP = """
[sweep]
schedule = "daily"

[[sweep.dirs]]
path = "/nonexistent"
idle = "1d"
"""


class ConfigTests(JanitorTestCase):
    def test_missing_config_stops_the_command(self):
        result = self.run_janitor("sweep", expected_status=2)
        self.assertIn("file not found", result.stderr)

    def test_unknown_key_stops_the_command(self):
        self.write_config(SWEEP.replace('idle = "1d"', 'idle = "1d"\nage = "2d"'))
        result = self.run_janitor("sweep", expected_status=2)
        self.assertIn("unknown key in sweep.dirs[0]: age", result.stderr)

    def test_invalid_duration_stops_the_command(self):
        for idle in ("1 day", "24", "1m", ""):
            with self.subTest(idle=idle):
                self.write_config(SWEEP.replace('"1d"', f'"{idle}"'))
                result = self.run_janitor("sweep", expected_status=2)
                self.assertIn("sweep.dirs[0].idle must be a duration", result.stderr)

    def test_relative_path_stops_the_command(self):
        self.write_config(SWEEP.replace('"/nonexistent"', '"tmp"'))
        result = self.run_janitor("sweep", expected_status=2)
        self.assertIn("must be absolute", result.stderr)

    def test_alert_percent_must_be_in_range(self):
        for percent in ("0", "101", "true", '"75"'):
            with self.subTest(percent=percent):
                self.write_config(SWEEP + f'[alert]\nschedule = "hourly"\npath = "/tmp"\npercent = {percent}\n')
                self.run_janitor("alert", expected_status=2)

    def test_schedule_prints_each_expression(self):
        self.write_config(
            SWEEP.replace('"daily"', '["06:00", "Mon *-*-* 18:00"]')
            + '[alert]\nschedule = "*:0/10"\npath = "/tmp"\npercent = 75\n'
        )
        self.assertEqual(self.run_janitor("schedule", "sweep").stdout, "06:00\nMon *-*-* 18:00\n")
        self.assertEqual(self.run_janitor("schedule", "alert").stdout, "*:0/10\n")

    def test_schedule_without_alert_prints_nothing(self):
        self.write_config(SWEEP)
        self.assertEqual(self.run_janitor("schedule", "alert").stdout, "")

    def test_empty_schedule_stops_the_command(self):
        for schedule in ('""', "[]", '["daily", ""]'):
            with self.subTest(schedule=schedule):
                self.write_config(SWEEP.replace('"daily"', schedule))
                self.run_janitor("schedule", "sweep", expected_status=2)

    def test_example_config_loads(self):
        self.env["TMP_JANITOR_CONFIG"] = str(REPO / "config.example.toml")
        self.assertEqual(self.run_janitor("schedule", "sweep").stdout, "daily\n")
        self.assertEqual(self.run_janitor("schedule", "alert").stdout, "*:0/10\n")


if __name__ == "__main__":
    unittest.main()
