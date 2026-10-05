"""Exercise update validation and rollback in disposable Git repositories."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile


UPDATER = Path(__file__).resolve().parents[1] / "update.py"
SPEC = importlib.util.spec_from_file_location("register_updater", UPDATER)
update = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update)

SMOKE = b"import unittest\nclass Smoke(unittest.TestCase):\n    def test_ok(self): self.assertTrue(True)\n"
FAIL = b"import unittest\nclass Smoke(unittest.TestCase):\n    def test_ok(self): self.fail('deliberate fixture failure')\n"


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="register-update-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "register"
        self.downloads = self.root / "Downloads"
        self.project.mkdir()
        self.downloads.mkdir()
        self.archive = self.downloads / "register_update.zip"
        self.original = {
            ".gitignore": (b"__pycache__/\n.register/\n*.zip\n", 0o644),
            "register": (b"old register\n", 0o755),
            "register_support.py": (b"# old support\n", 0o644),
            "install.sh": (b"#!/bin/sh\ntouch INSTALLER_WAS_RUN\n", 0o755),
            "tests/test_smoke.py": (SMOKE, 0o644),
            "old.txt": (b"old file\n", 0o644),
        }
        for name, (data, mode) in self.original.items():
            self.write(name, data, mode)
        # Browser downloads commonly lose execute permission. Bootstrap needs no chmod.
        self.write("update.py", UPDATER.read_bytes(), 0o644)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Register test")
        self.git("config", "user.email", "register-test@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("add", "--", ".")
        self.git("commit", "-m", "fixture baseline")
        self.old_head = self.git("rev-parse", "HEAD").stdout.strip()
        self.replacement = {
            **self.original,
            "register": (b"new register\n", 0o755),
            "update.py": (UPDATER.read_bytes(), 0o755),
            "docs/new.md": (b"new docs\n", 0o644),
        }
        self.replacement.pop("old.txt")

    def write(self, name, data, mode=0o644):
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.project), *args],
                                text=True, capture_output=True,
                                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def bundle(self, *, original=None, replacement=None, remove=None, mutate=None, extra=None):
        original = self.original if original is None else original
        replacement = self.replacement if replacement is None else replacement
        def records(entries):
            return [{"path": name, "sha256": hashlib.sha256(value[0]).hexdigest(), "mode": value[1]}
                    for name, value in entries.items()]
        manifest = {"kind": "register-update", "baseline": records(original),
                    "files": records(replacement), "remove": ["old.txt"] if remove is None else remove,
                    "commit_message": "Simplify register"}
        if mutate:
            mutate(manifest)
        with zipfile.ZipFile(self.archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("manifest.json", json.dumps(manifest))
            for prefix, entries in (("baseline", original), ("files", replacement)):
                for name, (data, mode) in entries.items():
                    bundle.writestr(prefix + "/" + name, data)
            bundle.writestr("update.py", replacement["update.py"][0])
            for name, data in (extra or {}).items():
                bundle.writestr(name, data)
        return manifest

    def apply(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return update.apply_update(self.project, self.archive, self.downloads)

    def assert_unchanged(self):
        self.assertTrue(self.archive.exists())
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), self.old_head)
        self.assertEqual((self.project / "register").read_bytes(), self.original["register"][0])

    def test_success_tests_then_commit_delete_zip_without_install(self):
        self.write(".register/register.sqlite3", b"saved private buffers")
        self.bundle()
        self.assertEqual(self.apply(), 0)
        self.assertFalse(self.archive.exists())
        self.assertEqual(self.git("log", "-1", "--format=%s").stdout.strip(), "Simplify register")
        self.assertEqual((self.project / "register").read_bytes(), b"new register\n")
        self.assertFalse((self.project / "old.txt").exists())
        self.assertEqual((self.project / "docs/new.md").read_bytes(), b"new docs\n")
        self.assertEqual(stat.S_IMODE((self.project / "update.py").stat().st_mode), 0o755)
        self.assertEqual((self.project / ".register/register.sqlite3").read_bytes(), b"saved private buffers")
        self.assertFalse((self.project / "INSTALLER_WAS_RUN").exists())
        self.assertFalse(self.git("status", "--porcelain", "--untracked-files=all").stdout)

    def test_default_cli_uses_script_project_and_download_directory(self):
        self.bundle()
        result = subprocess.run([sys.executable, str(self.project / "update.py"),
                                 "--downloads", str(self.downloads)], cwd=self.root,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.archive.exists())

    def test_default_cli_selects_newest_browser_download_and_keeps_old_failed_zip(self):
        self.archive.write_bytes(b"old failed ZIP")
        old = self.archive
        os.utime(old, ns=(1, 1))
        latest = self.downloads / "register_update (2).zip"
        self.archive = latest
        self.bundle()
        other_duplicate = self.downloads / "register_update(1).zip"
        other_duplicate.write_bytes(b"older browser duplicate")
        os.utime(other_duplicate, ns=(2, 2))
        unrelated = self.downloads / "register_update_other.zip"
        unrelated.write_bytes(b"newer unrelated ZIP")
        os.utime(unrelated, ns=(latest.stat().st_mtime_ns + 1, latest.stat().st_mtime_ns + 1))
        result = subprocess.run([sys.executable, str(self.project / "update.py"),
                                 "--downloads", str(self.downloads)], cwd=self.root,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(latest.exists())
        self.assertEqual(old.read_bytes(), b"old failed ZIP")
        self.assertEqual(other_duplicate.read_bytes(), b"older browser duplicate")
        self.assertTrue(unrelated.exists())
        # Both common browser spellings are recognized by discovery.
        self.assertEqual(update._newest_archive(self.downloads), other_duplicate)

    def test_explicit_archive_outside_downloads_is_kept(self):
        self.bundle()
        other = self.root / "outside.zip"
        self.archive.replace(other)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(update.apply_update(self.project, other, self.downloads), 0)
        self.assertTrue(other.exists())

    def test_untracked_or_tracked_changes_refuse_before_writes(self):
        for tracked in (False, True):
            with self.subTest(tracked=tracked):
                name = "notes.txt" if not tracked else "register"
                self.write(name, b"local changes", 0o755 if tracked else 0o644)
                self.bundle()
                with self.assertRaisesRegex(update.UpdateError, "uncommitted"):
                    self.apply()
                self.assertTrue(self.archive.exists())
                self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), self.old_head)
                self.assertEqual((self.project / name).read_bytes(), b"local changes")
                if tracked:
                    self.write(name, *self.original[name])
                else:
                    (self.project / name).unlink()

    def test_changed_committed_source_refuses_baseline(self):
        self.write("register", b"other committed work\n", 0o755)
        self.git("add", "register")
        self.git("commit", "-m", "other work")
        self.bundle()
        with self.assertRaisesRegex(update.UpdateError, "differs from"):
            self.apply()
        self.assertTrue(self.archive.exists())
        self.assertEqual((self.project / "register").read_bytes(), b"other committed work\n")

    def test_corrupt_checksum_and_undeclared_members_refuse(self):
        for corruption in ("checksum", "extra"):
            with self.subTest(corruption=corruption):
                mutation = (lambda manifest: manifest["files"][0].update(sha256="0" * 64)) if corruption == "checksum" else None
                self.bundle(mutate=mutation, extra={"undeclared.txt": b"x"} if corruption == "extra" else None)
                with self.assertRaises(update.UpdateError):
                    self.apply()
                self.assert_unchanged()

    def test_traversal_runtime_and_symlink_payloads_refuse(self):
        for name in ("../escape", ".git/config", ".register/buffer", "buffers.sqlite3", "/absolute"):
            with self.subTest(name=name):
                replacement = {**self.replacement, name: (b"x", 0o644)}
                self.bundle(replacement=replacement)
                with self.assertRaises(update.UpdateError):
                    self.apply()
                self.assert_unchanged()
        self.bundle()
        with zipfile.ZipFile(self.archive, "a") as bundle:
            member = zipfile.ZipInfo("link")
            member.create_system = 3
            member.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(member, "../outside")
        with self.assertRaisesRegex(update.UpdateError, "special file"):
            self.apply()
        self.assert_unchanged()

    def test_existing_symlink_parent_is_never_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.project / "docs").symlink_to(outside, target_is_directory=True)
        self.git("add", "docs")
        self.git("commit", "-m", "project symlink")
        self.bundle()
        with self.assertRaisesRegex(update.UpdateError, "symlink"):
            self.apply()
        self.assertFalse((outside / "new.md").exists())
        self.assertTrue(self.archive.exists())

    def test_test_failure_restores_files_modes_index_and_new_directories(self):
        replacement = {**self.replacement, "tests/test_smoke.py": (FAIL, 0o644)}
        self.bundle(replacement=replacement)
        old_index = (self.project / ".git/index").read_bytes()
        with self.assertRaisesRegex(update.UpdateError, "Tests failed"):
            self.apply()
        self.assert_unchanged()
        self.assertEqual((self.project / "tests/test_smoke.py").read_bytes(), SMOKE)
        self.assertEqual((self.project / ".git/index").read_bytes(), old_index)
        self.assertEqual(stat.S_IMODE((self.project / "update.py").stat().st_mode), 0o644)
        self.assertTrue((self.project / "old.txt").exists())
        self.assertFalse((self.project / "docs").exists())
        self.assertFalse(self.git("status", "--porcelain", "--untracked-files=all").stdout)

    def test_commit_failure_restores_index_and_files(self):
        hook = self.project / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        self.bundle()
        old_index = (self.project / ".git/index").read_bytes()
        with self.assertRaisesRegex(update.UpdateError, "Command failed"):
            self.apply()
        self.assert_unchanged()
        self.assertEqual((self.project / ".git/index").read_bytes(), old_index)
        self.assertFalse(self.git("status", "--porcelain", "--untracked-files=all").stdout)

    def test_active_merge_and_missing_repository_refuse(self):
        self.bundle()
        merge = self.project / ".git/MERGE_HEAD"
        merge.write_text(self.old_head + "\n")
        with self.assertRaisesRegex(update.UpdateError, "Git operation"):
            self.apply()
        merge.unlink()
        self.assert_unchanged()
        other = self.root / "not-a-project"
        other.mkdir()
        with self.assertRaisesRegex(update.UpdateError, "existing Git repository"):
            update.apply_update(other, self.archive, self.downloads)
        self.assertFalse(list(other.iterdir()))

    def test_unexpected_commit_is_preserved(self):
        unexpected = (b"import subprocess, unittest\n"
                      b"class Smoke(unittest.TestCase):\n"
                      b"    def test_ok(self):\n"
                      b"        subprocess.run(['git', 'commit', '--allow-empty', '-m', 'unexpected commit'], check=True, capture_output=True)\n")
        self.bundle(replacement={**self.replacement, "tests/test_smoke.py": (unexpected, 0o644)})
        with self.assertRaisesRegex(update.UpdateError, "HEAD changed"):
            self.apply()
        self.assertTrue(self.archive.exists())
        self.assertEqual(self.git("log", "-1", "--format=%s").stdout.strip(), "unexpected commit")
        self.assertFalse((self.project / ".git/register-update.lock").exists())


if __name__ == "__main__":
    unittest.main()
