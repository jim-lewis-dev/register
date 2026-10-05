"""Test completion branches directly in Bash and verify names stay live."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]
COMPLETION = PROJECT / "completion/register.bash"
BASH = r'''
source "$1"
COMP_CWORD=$2
shift 2
COMP_WORDS=("$@")
register() {
    printf '%s\n' "$*" >> "$QUERY_LOG"
    if [[ ${REAL_REGISTER-} ]]; then
        "$PYTHON_BIN" "$REAL_REGISTER" "$@"
    elif [[ ${2-} == --exec ]]; then
        compgen -W 'beta' -- "${3-}"
    else
        compgen -W 'alpha beta zeta' -- "${2-}"
    fi
}
compopt() { printf '%s\n' "$*" >> "$OPTIONS_LOG"; }
_register_completion
if (( ${#COMPREPLY[@]} )); then printf '%s\0' "${COMPREPLY[@]}"; fi
exit 0
'''


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.query_log = self.root / "queries.log"
        self.options_log = self.root / "options.log"
        self.env = os.environ.copy()
        self.env.update({
            "QUERY_LOG": str(self.query_log),
            "OPTIONS_LOG": str(self.options_log),
            "REGISTER_HOME": str(self.root / "store"),
            "PYTHON_BIN": sys.executable,
        })
        self.env.pop("REAL_REGISTER", None)
        (self.root / "fixture file.pdf").write_bytes(b"file")
        (self.root / "-fixture").write_bytes(b"dash file")
        (self.root / "CLIPBOARD").write_bytes(b"ordinary file")

    def complete(self, *words, real=False):
        self.query_log.write_text("")
        self.options_log.write_text("")
        env = self.env.copy()
        if real:
            env["REAL_REGISTER"] = str(PROJECT / "register")
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", BASH, "completion-test",
             str(COMPLETION), str(len(words) - 1), *words],
            cwd=self.root, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        return [word.decode() for word in result.stdout.split(b"\0") if word]

    def test_commands_and_only_current_flags_are_suggested(self):
        self.assertEqual(set(self.complete("register", "")),
                         {"add", "update", "copy", "get", "show", "remove", "run"})
        flags = self.complete("register", "update", "--")
        self.assertEqual(set(flags),
                         {"--exec", "--no-exec", "--utf8", "--binary", "--mime", "--help", "--"})
        self.assertEqual(set(self.complete("register", "get", "--")), {"--help", "--"})
        self.assertEqual(self.complete("register", "get", "--mime="), [])

    def test_existing_name_positions_query_live_names_after_flags(self):
        for command in ("update", "get", "show", "remove", "copy"):
            with self.subTest(command=command):
                self.assertEqual(self.complete("register", command, "b"), ["beta"])
                self.assertEqual(self.query_log.read_text(), "_complete b\n")
        self.assertEqual(self.complete("register", "add", "b"), [])
        self.assertEqual(self.query_log.read_text(), "")
        self.assertEqual(self.complete("register", "update", "--binary", "--exec", "b"), ["beta"])
        self.assertEqual(self.complete("register", "copy", "--mime", "text/plain", "alpha", "b"), ["beta"])
        self.assertEqual(self.complete("register", "show", "alpha", "b"), ["beta"])

    def test_object_positions_suggest_files_and_clipboard(self):
        for command in ("add", "update"):
            with self.subTest(command=command):
                values = self.complete("register", command, "--exec", "note", "")
                self.assertIn("fixture file.pdf", values)
                self.assertIn("CLIPBOARD", values)
                self.assertIn("./CLIPBOARD", values)
                self.assertEqual(values.count("CLIPBOARD"), 1)
                self.assertIn("./-fixture", values)
                self.assertNotIn("-", values)
                self.assertNotIn("PRIMARY", values)
                self.assertNotIn("beta", values)
                self.assertEqual(self.query_log.read_text(), "")
                self.assertEqual(self.complete("register", command, "note", "fi"), ["fixture file.pdf"])
                self.assertNotIn("-", self.complete("register", command, "note", "-"))
        self.assertEqual(self.complete("register", "add", "note", "-", ""), [])

    def test_get_destination_only_suggests_clipboard(self):
        self.assertEqual(self.complete("register", "get", "note", ""), ["CLIPBOARD"])
        self.assertEqual(self.complete("register", "get", "note", "CL"), ["CLIPBOARD"])
        self.assertEqual(self.complete("register", "get", "note", "fi"), [])
        self.assertEqual(self.complete("register", "get", "note", "CLIPBOARD", ""), [])

    def test_run_suggests_executable_names_then_delegates_arguments(self):
        self.assertEqual(self.complete("register", "run", ""), ["beta"])
        self.assertEqual(self.query_log.read_text(), "_complete --exec \n")
        self.assertEqual(self.complete("register", "run", "--", "b"), ["beta"])
        self.assertEqual(self.complete("register", "run", "beta", "--mime="), [])
        self.assertEqual(self.query_log.read_text(), "")
        self.assertEqual(self.options_log.read_text(), "-o default\n")

    def test_end_of_options_preserves_leading_dash_objects(self):
        self.assertEqual(self.complete("register", "add", "note", "--", "-fi"), ["-fixture"])
        self.assertEqual(self.query_log.read_text(), "")
        self.assertEqual(self.complete("register", "add", "note", "--", "-fixture", ""), [])
        self.assertEqual(self.complete("register", "update", "--", "b"), ["beta"])

    def test_mime_values_and_equal_forms_do_not_count_as_names(self):
        self.assertEqual(self.complete("register", "add", "note", "--mime", "application/p"),
                         ["application/pdf"])
        self.assertEqual(self.complete("register", "add", "note", "--mime=application/p"),
                         ["--mime=application/pdf"])
        self.assertEqual(self.complete("register", "add", "note", "--mime", "=", "application/p"),
                         ["application/pdf"])
        self.assertEqual(self.complete("register", "update", "--mime", "=", "text/plain", "b"), ["beta"])
        self.assertEqual(self.complete("register", "update", "--mime=text/plain", "b"), ["beta"])
        self.assertEqual(self.complete("register", "add", "--mime", "text/plain", "note", "fi"),
                         ["fixture file.pdf"])
        self.assertEqual(self.complete("register", "add", "note", "--", "--mime=application/p"), [])

    def test_names_change_without_reinstalling_completion(self):
        def cli(*arguments, content=b""):
            result = subprocess.run(
                [sys.executable, str(PROJECT / "register"), *arguments],
                input=content, env=self.env, cwd=self.root,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(self.complete("register", "get", "", real=True), [])
        cli("add", "alpha", content=b"text")
        self.assertEqual(self.complete("register", "get", "", real=True), ["alpha"])
        cli("add", "beta", "--exec", content=b"printf hello\n")
        self.assertEqual(self.complete("register", "get", "", real=True), ["alpha", "beta"])
        self.assertEqual(self.complete("register", "run", "", real=True), ["beta"])
        cli("remove", "beta")
        self.assertEqual(self.complete("register", "run", "", real=True), [])


if __name__ == "__main__":
    unittest.main()
