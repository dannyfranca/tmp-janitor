import os
import shutil
import socket
import stat
import subprocess
import sys
import time
import unittest

from support import SCRIPT, JanitorTestCase


IDLE_SECONDS = 3600


class SweepTests(JanitorTestCase):
    def setUp(self):
        super().setUp()
        self.target = self.root / "target"
        self.target.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.use_config(self.target)
        # Files cannot get an old ctime, so the clock moves forward past the idle time instead.
        self.later = time.time() + 2 * IDLE_SECONDS
        self.env["TMP_JANITOR_NOW"] = str(self.later)

    def use_config(self, path, extra=""):
        self.write_config(
            f"""
[sweep]
schedule = "daily"
keep = ["keep-*"]
{extra}

[[sweep.dirs]]
path = "{path}"
idle = "1h"
"""
        )

    def make_tree(self, name):
        tree = self.target / name
        (tree / "nested").mkdir(parents=True)
        (tree / "nested" / "file").write_text("data")
        return tree

    def test_removes_idle_entries(self):
        tree = self.make_tree("build")
        loose = self.target / "notes.txt"
        loose.write_text("notes")
        result = self.run_janitor("sweep")
        self.assertFalse(tree.exists())
        self.assertFalse(loose.exists())
        self.assertIn(f"removed {tree}", result.stdout)
        self.assertIn(f"{self.target}: removed 2 entries", result.stdout)

    def test_removes_symlink_without_its_target(self):
        (self.outside / "kept").write_text("data")
        link = self.target / "link"
        link.symlink_to(self.outside)
        self.run_janitor("sweep")
        self.assertFalse(link.is_symlink())
        self.assertTrue((self.outside / "kept").exists())

    def test_keeps_entry_with_a_recent_change(self):
        tree = self.make_tree("active")
        os.utime(tree / "nested" / "file", (self.later, self.later))
        self.run_janitor("sweep")
        self.assertTrue(tree.exists())

    def test_keeps_names_that_match_keep(self):
        tree = self.make_tree("keep-me")
        self.run_janitor("sweep")
        self.assertTrue(tree.exists())

    def test_keeps_entry_that_a_process_uses_as_cwd(self):
        tree = self.make_tree("cwd")
        process = subprocess.Popen(["sleep", "30"], cwd=tree / "nested")
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        result = self.run_janitor("sweep")
        self.assertTrue(tree.exists())
        self.assertIn(f"kept {tree}: in use", result.stdout)

    def test_keeps_entry_with_an_open_file(self):
        tree = self.make_tree("open")
        with open(tree / "nested" / "file") as handle:
            self.run_janitor("sweep")
            self.assertTrue(tree.exists())
            self.assertEqual(handle.read(), "data")

    def test_keeps_entry_with_a_running_executable(self):
        tree = self.make_tree("bin")
        executable = tree / "nested" / "sleep"
        shutil.copy2(shutil.which("sleep"), executable)
        process = subprocess.Popen([executable, "30"], cwd=self.root)
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        self.run_janitor("sweep")
        self.assertTrue(tree.exists())

    def test_keeps_entry_with_a_mapped_file_after_its_fd_closes(self):
        tree = self.make_tree("mapped")
        # Python mmap keeps its own copy of the fd, so only a raw mmap call leaves the map without an fd.
        code = (
            "import ctypes, os, sys, time\n"
            "libc = ctypes.CDLL(None, use_errno=True)\n"
            "libc.mmap.restype = ctypes.c_void_p\n"
            "libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]\n"
            "fd = os.open(sys.argv[1], os.O_RDONLY)\n"
            "assert libc.mmap(None, 4, 1, 2, fd, 0) not in (None, ctypes.c_void_p(-1).value)\n"
            "os.close(fd)\n"
            "print('ready', flush=True)\n"
            "time.sleep(30)\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", code, tree / "nested" / "file"], cwd=self.root, stdout=subprocess.PIPE, text=True
        )
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        self.addCleanup(process.stdout.close)
        self.assertEqual(process.stdout.readline(), "ready\n")
        self.run_janitor("sweep")
        self.assertTrue(tree.exists())

    def test_keeps_entry_with_a_recent_status_change(self):
        tree = self.make_tree("extracted")
        old = time.time() - 7 * 86400
        for path in (tree / "nested" / "file", tree / "nested", tree):
            os.utime(path, (old, old))
        self.env["TMP_JANITOR_NOW"] = str(time.time() + IDLE_SECONDS / 2)
        self.run_janitor("sweep")
        self.assertTrue(tree.exists())

    def test_keeps_entry_that_contains_a_mount(self):
        unshare = ["unshare", "--user", "--map-root-user", "--mount"]
        probe = subprocess.run([*unshare, "mount", "--bind", self.root, self.root], capture_output=True, timeout=30)
        if probe.returncode != 0:
            self.skipTest("needs a user namespace that can mount")
        tree = self.make_tree("mounted")
        source = self.outside / "persistent"
        source.mkdir()
        (source / "data").write_text("data")
        mount_point = tree / "nested" / "mount"
        mount_point.mkdir()
        result = subprocess.run(
            [*unshare, "sh", "-c", 'mount --bind "$1" "$2" && exec "$3" "$4" sweep', "sh",
             source, mount_point, sys.executable, SCRIPT],
            env=self.env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"kept {tree}: contains a mount", result.stdout)
        self.assertTrue((source / "data").exists())

    def test_keeps_runtime_dirs_and_sockets(self):
        runtime = self.target / "runtime"
        runtime.mkdir()
        for path in (runtime / "server.sock", self.target / "top.sock"):
            server = socket.socket(socket.AF_UNIX)
            server.bind(str(path))
            self.addCleanup(server.close)
        self.run_janitor("sweep")
        self.assertTrue((runtime / "server.sock").exists())
        self.assertTrue((self.target / "top.sock").exists())

    def test_dry_run_removes_nothing(self):
        tree = self.make_tree("build")
        result = self.run_janitor("sweep", "--dry-run")
        self.assertTrue(tree.exists())
        self.assertIn(f"would remove {tree}", result.stdout)

    def test_dry_run_from_config_removes_nothing(self):
        self.use_config(self.target, extra="dry_run = true")
        tree = self.make_tree("build")
        self.run_janitor("sweep")
        self.assertTrue(tree.exists())

    def test_removes_read_only_trees(self):
        tree = self.make_tree("gomodcache")
        (tree / "nested" / "file").chmod(stat.S_IRUSR)
        (tree / "nested").chmod(stat.S_IRUSR | stat.S_IXUSR)
        tree.chmod(stat.S_IRUSR | stat.S_IXUSR)
        self.addCleanup(lambda: tree.exists() and tree.chmod(stat.S_IRWXU))
        self.addCleanup(lambda: (tree / "nested").exists() and (tree / "nested").chmod(stat.S_IRWXU))
        self.run_janitor("sweep")
        self.assertFalse(tree.exists())

    def test_skips_a_missing_dir(self):
        self.use_config(self.root / "missing")
        result = self.run_janitor("sweep")
        self.assertIn("not a dir", result.stdout)

    def test_refuses_the_home_dir(self):
        (self.home / "file").write_text("data")
        self.use_config(self.home)
        result = self.run_janitor("sweep", expected_status=1)
        self.assertIn("refusing", result.stderr)
        self.assertTrue((self.home / "file").exists())

    def test_expands_the_home_dir_in_paths(self):
        scratch = self.home / "scratch"
        scratch.mkdir()
        (scratch / "old").write_text("data")
        self.use_config("~/scratch")
        self.run_janitor("sweep")
        self.assertFalse((scratch / "old").exists())


if __name__ == "__main__":
    unittest.main()
