"""Installer integration checks with disposable homes and a mocked package manager."""

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]
LAUNCHER_MARKER = "# register launcher: managed by install.sh\n"
COMPLETION_MARKER = "# register completion: managed by install.sh\n"
LEGACY_BLOCK = '''# >>> register installer >>>
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) export PATH="$HOME/.local/bin:$PATH" ;;
esac
if [[ -r "$HOME/.local/share/bash-completion/completions/register" ]]; then
    source "$HOME/.local/share/bash-completion/completions/register"
fi
# <<< register installer <<<
'''


class InstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="register_install_test_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.project = self.root / "project's source files"
        self.project.mkdir()
        for name in ("install.sh", "uninstall.sh"):
            shutil.copy2(PROJECT / name, self.project / name)
        (self.project / "register_support.py").write_text('VALUE = "from project"\n')
        (self.project / "register").write_text(
            'import json, sys, register_support\n'
            'print(json.dumps([register_support.VALUE, *sys.argv[1:]]))\n'
        )
        (self.project / "completion").mkdir()
        self.set_completion("first")
        self.bashrc = self.home / ".bashrc"
        self.bashrc.write_text("# Existing user settings\nexport USER_SETTING=kept\n")
        self.bashrc.chmod(0o640)
        self.mock_bin = self.root / "mock-bin"
        self.mock_bin.mkdir()
        apt = self.mock_bin / "apt-get"
        apt.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$TEST_APT_LOG"\n')
        apt.chmod(0o755)
        sudo = self.mock_bin / "sudo"
        sudo.write_text('#!/bin/bash\nexec "$@"\n')
        sudo.chmod(0o755)
        self.apt_log = self.root / "apt.log"
        self.env = {**os.environ, "HOME": str(self.home),
                    "PATH": str(self.mock_bin) + os.pathsep + os.environ["PATH"],
                    "TEST_APT_LOG": str(self.apt_log)}
        self.launcher = self.home / ".local/bin/register"
        self.completion = self.home / ".local/share/bash-completion/completions/register"

    def set_completion(self, word):
        (self.project / "completion/register.bash").write_text(
            COMPLETION_MARKER + '_register_completion() { COMPREPLY=(' + word + '); }\n'
            'complete -F _register_completion register\n'
        )

    def script(self, name, *arguments, success=True, env=None):
        result = subprocess.run(
            [str(self.project / name), *arguments], cwd=self.root, env=env or self.env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr.decode())
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def shell(self, command, env=None):
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", command], env=env or self.env,
            cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result.stdout.decode()

    def test_plain_install_exposes_external_launcher_without_package_operations(self):
        self.script("install.sh")
        self.assertFalse(self.apt_log.exists())
        result = self.shell('source "$HOME/.bashrc"; register "two words" --flag')
        self.assertEqual(json.loads(result), ["from project", "two words", "--flag"])
        kinds = self.shell('source "$HOME/.bashrc"; type -t register; type -t _register_completion')
        self.assertEqual(kinds.splitlines(), ["file", "function"])
        self.assertTrue(os.access(self.launcher, os.X_OK))
        self.assertEqual(stat.S_IMODE(self.bashrc.stat().st_mode), 0o640)

    def test_installed_completion_follows_project_without_reinstall(self):
        self.script("install.sh")
        wrapper_before = self.completion.read_bytes()
        command = 'source "$HOME/.bashrc"; _register_completion; printf "%s" "${COMPREPLY[0]}"'
        self.assertEqual(self.shell(command), "first")
        self.set_completion("updated")
        self.assertEqual(self.shell(command), "updated")
        self.assertEqual(self.completion.read_bytes(), wrapper_before)
        self.assertNotIn("COMPREPLY", self.completion.read_text())

    def test_reinstall_migrates_old_combined_block_and_is_idempotent(self):
        self.bashrc.write_text("# User prefix\n" + LEGACY_BLOCK + "# User suffix\n")
        self.script("install.sh")
        once = self.bashrc.read_bytes()
        self.script("install.sh")
        self.assertEqual(self.bashrc.read_bytes(), once)
        self.assertEqual(once.count(b"# >>> register installer >>>"), 1)
        self.assertEqual(once.count(b"# >>> user local bin >>>"), 1)
        self.assertIn(b"# User prefix\n", once)
        self.assertIn(b"# User suffix\n", once)
        self.assertEqual(list(self.home.glob(".bashrc-register-*")), [])

    def test_uninstall_keeps_project_buffers_packages_and_shared_user_path(self):
        self.script("install.sh")
        data = self.home / ".register"
        data.mkdir()
        (data / "register.sqlite3").write_bytes(b"saved buffer database")
        other = self.home / ".local/bin/other-user-tool"
        other.write_text("#!/bin/sh\nexit 0\n")
        other.chmod(0o755)
        self.script("uninstall.sh")
        self.assertFalse(self.launcher.exists())
        self.assertFalse(self.completion.exists())
        self.assertTrue((self.project / "register").exists())
        self.assertEqual((data / "register.sqlite3").read_bytes(), b"saved buffer database")
        self.assertFalse(self.apt_log.exists())
        self.assertNotIn("# >>> register installer >>>", self.bashrc.read_text())
        self.assertEqual(self.shell('source "$HOME/.bashrc"; command -v other-user-tool').strip(), str(other))
        self.script("install.sh")
        self.script("uninstall.sh")
        self.assertEqual(self.bashrc.read_text().count("# >>> user local bin >>>"), 1)

    def test_uninstall_old_install_preserves_its_shared_path(self):
        self.bashrc.write_text("# User prefix\n" + LEGACY_BLOCK + "# User suffix\n")
        self.launcher.parent.mkdir(parents=True)
        self.launcher.write_text(LAUNCHER_MARKER)
        self.completion.parent.mkdir(parents=True)
        self.completion.write_text(COMPLETION_MARKER)
        other = self.launcher.parent / "other-user-tool"
        other.write_text("#!/bin/sh\nexit 0\n")
        other.chmod(0o755)
        self.script("uninstall.sh")
        self.assertEqual(self.shell('source "$HOME/.bashrc"; command -v other-user-tool').strip(), str(other))
        self.assertIn("# User prefix\n", self.bashrc.read_text())
        self.assertIn("# User suffix\n", self.bashrc.read_text())
        self.assertNotIn("source ", self.bashrc.read_text())

    def test_unrelated_existing_hooks_are_not_overwritten_or_removed(self):
        self.launcher.parent.mkdir(parents=True)
        self.launcher.write_bytes(b"unrelated user command\n")
        previous_bashrc = self.bashrc.read_bytes()
        failure = self.script("install.sh", success=False)
        self.assertIn(b"unrelated", failure.stderr)
        self.assertEqual(self.launcher.read_bytes(), b"unrelated user command\n")
        self.assertEqual(self.bashrc.read_bytes(), previous_bashrc)
        self.script("uninstall.sh")
        self.assertEqual(self.launcher.read_bytes(), b"unrelated user command\n")
        self.assertEqual(self.bashrc.read_bytes(), previous_bashrc)

    def test_dependency_flag_uses_mock_apt_then_installs_hooks(self):
        # Only installer necessities exist on PATH; optional desktop helpers are
        # deliberately absent. No real apt or sudo can be reached by this test.
        for command in ("bash", "dirname", "grep", "mkdir", "mktemp", "chmod", "mv", "rm"):
            (self.mock_bin / command).symlink_to(shutil.which(command))
        (self.mock_bin / "python3").symlink_to(sys.executable)
        environment = {**self.env, "PATH": str(self.mock_bin)}
        self.script("install.sh", "--dependencies", env=environment)
        self.assertEqual(self.apt_log.read_text().splitlines(),
                         ["update", "install -y file xclip wl-clipboard"])
        self.assertTrue(self.launcher.exists())
        self.assertTrue(self.completion.exists())
        self.assertIn("# >>> register installer >>>", self.bashrc.read_text())

    def test_shell_configuration_symlink_is_preserved(self):
        target = self.root / "managed-bashrc"
        target.write_text("# User dotfiles target\n")
        target.chmod(0o600)
        self.bashrc.unlink()
        self.bashrc.symlink_to(target)
        self.script("install.sh")
        self.assertTrue(self.bashrc.is_symlink())
        self.assertIn("# >>> register installer >>>", target.read_text())
        self.script("uninstall.sh")
        self.assertTrue(self.bashrc.is_symlink())
        self.assertNotIn("# >>> register installer >>>", target.read_text())
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
