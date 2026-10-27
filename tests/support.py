import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "bin" / "tmp-janitor"


class JanitorTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tmp-janitor-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.config = self.root / "config.toml"
        self.env = os.environ.copy()
        self.env.update(
            HOME=str(self.home),
            TMP_JANITOR_CONFIG=str(self.config),
            XDG_STATE_HOME=str(self.root / "state"),
        )
        self.env.pop("TMP_JANITOR_NOW", None)

    def write_config(self, text):
        self.config.write_text(text)

    def run_janitor(self, *args, expected_status=0):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, expected_status, result.stdout + result.stderr)
        return result
