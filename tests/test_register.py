"""Behavior checks for the command line; every test uses its own empty store.

Clipboard checks use simulated desktop helpers. They verify Register's backend
commands, MIME choices, and byte preservation without needing a live desktop.
"""

import base64
import json
import os
import pty
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest


PROJECT = Path(__file__).resolve().parents[1]
COMMAND = [sys.executable, str(PROJECT / "register")]
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO+a7X8AAAAASUVORK5CYII="
)


class CLIBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = self.root / "store"
        self.env = os.environ.copy()
        self.env["REGISTER_HOME"] = str(self.store)
        # Avoid accidental contact with an inherited graphical session.
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE"):
            self.env.pop(key, None)

    def cli(self, *args, data=b"", code=0, cwd=None, env=None):
        result = subprocess.run(
            COMMAND + list(args), input=data, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env or self.env,
            cwd=cwd or self.root, timeout=15,
        )
        diagnostic = result.stderr.decode("utf-8", "replace")
        if code is None:
            self.assertNotEqual(result.returncode, 0, diagnostic)
            self.assertNotIn("Traceback", diagnostic)
        else:
            self.assertEqual(result.returncode, code, diagnostic)
        return result

    def metadata(self, name):
        return json.loads(self.cli("show", name).stdout)

    def cli_tty(self, *args):
        """Keep stdin genuinely interactive while still capturing diagnostics."""
        master, slave = pty.openpty()
        try:
            result = subprocess.run(
                COMMAND + list(args), stdin=slave, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=self.env, cwd=self.root, timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
            return result
        finally:
            os.close(slave)
            os.close(master)


class RegisterCLI(CLIBase):
    def test_exact_text_retrieval_and_quiet_mutation(self):
        content = "café\nlast line without newline".encode()
        self.assertEqual(self.cli("add", "note", data=content).stdout, b"")
        self.assertEqual(self.cli("get", "note").stdout, content)
        meta = self.metadata("note")
        self.assertEqual(meta["name"], "note")
        self.assertEqual(meta["size"], len(content))
        self.assertTrue(meta["utf8"])
        self.assertFalse(meta["exec"])
        self.assertEqual(meta["mime"].split(";", 1)[0], "text/plain")

    def test_empty_stdin_is_valid_content_and_clears(self):
        self.cli("add", "note", data=b"old contents")
        self.cli("update", "note", data=b"")
        self.assertEqual(self.cli("get", "note").stdout, b"")
        self.assertEqual(self.metadata("note")["size"], 0)
        self.cli("add", "empty", data=b"")
        self.assertEqual(self.cli("get", "empty").stdout, b"")

    def test_dash_is_an_ordinary_object_and_omitted_object_reads_stdin(self):
        self.cli("add", "dash_text", "-", data=b"ignored stdin")
        self.assertEqual(self.cli("get", "dash_text").stdout, b"-")
        (self.root / "-").write_bytes(b"dash file contents")
        self.cli("add", "dash_file", "-", data=b"ignored stdin")
        self.assertEqual(self.cli("get", "dash_file").stdout, b"dash file contents")
        self.cli("add", "note", data=b"one")
        self.cli("update", "note", data=b"two")
        self.assertEqual(self.cli("get", "note").stdout, b"two")

    def test_source_file_is_copied_not_linked(self):
        source = self.root / "filename with spaces.json"
        source.write_bytes(b'{"answer":42}\n')
        self.cli("add", "config", str(source))
        source.write_bytes(b"changed")
        source.unlink()
        self.assertEqual(self.cli("get", "config").stdout, b'{"answer":42}\n')

    def test_literal_objects_are_quiet_and_have_no_added_newline(self):
        for index, text in enumerate(("hello", "hello world", "report.pdf",
                                      "./missing/report", "", "PRIMARY")):
            with self.subTest(text=text):
                name = "literal_%d" % index
                result = self.cli("add", name, text)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, b"")
                self.assertEqual(self.cli("get", name).stdout, text.encode())
                self.assertFalse(self.metadata(name)["exec"])
        self.cli("add", "PRIMARY", "ordinary name")
        self.assertEqual(self.cli("get", "PRIMARY").stdout, b"ordinary name")

    def test_long_literal_text_and_inline_command_do_not_fail_as_paths(self):
        text = "hello " * 80
        self.cli("add", "long_text", text)
        self.assertEqual(self.cli("get", "long_text").stdout, text.encode())
        script = "# " + "comment " * 80 + "\nprintf long-command"
        self.cli("add", "long_command", "--exec", script)
        self.assertEqual(self.cli("get", "long_command").stdout, script.encode())
        self.assertEqual(self.cli("run", "long_command").stdout, b"long-command")

    def test_object_with_existing_file_as_path_prefix_becomes_literal(self):
        parent_file = self.root / "ordinary-file"
        parent_file.write_bytes(b"unrelated bytes")
        text = str(parent_file / "not-a-child")
        result = self.cli("add", "path_text", text)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(self.cli("get", "path_text").stdout, text.encode())

    def test_existing_file_wins_and_piped_filename_remains_literal(self):
        source = self.root / "report.pdf"
        content = b"%PDF-1.4\nfile contents\n"
        source.write_bytes(content)
        self.cli("add", "document", source.name)
        self.assertEqual(self.cli("get", "document").stdout, content)
        self.cli("add", "literal", data=source.name.encode())
        self.assertEqual(self.cli("get", "literal").stdout, b"report.pdf")

    def test_explicit_object_wins_over_stdin_on_add_and_update(self):
        self.cli("add", "note", "explicit text", data=b"ignored input")
        self.assertEqual(self.cli("get", "note").stdout, b"explicit text")
        source = self.root / "input.txt"
        source.write_bytes(b"file bytes")
        self.cli("update", "note", str(source), data=b"ignored input")
        self.assertEqual(self.cli("get", "note").stdout, b"file bytes")
        self.cli("update", "note", "", data=b"ignored input")
        self.assertEqual(self.cli("get", "note").stdout, b"")

    def test_add_infers_executable_file_permission_and_honors_overrides(self):
        executable = self.root / "script"
        executable.write_bytes(b"printf inferred")
        executable.chmod(0o751)
        self.cli("add", "inferred", str(executable))
        self.assertTrue(self.metadata("inferred")["exec"])
        self.assertEqual(self.cli("run", "inferred").stdout, b"inferred")
        self.cli("add", "disabled", str(executable), "--no-exec")
        self.assertFalse(self.metadata("disabled")["exec"])
        executable.chmod(0o640)
        self.cli("add", "ordinary", str(executable))
        self.assertFalse(self.metadata("ordinary")["exec"])
        self.cli("add", "forced", str(executable), "--exec")
        self.assertTrue(self.metadata("forced")["exec"])
        self.assertEqual(self.cli("run", "forced").stdout, b"inferred")

    def test_update_preserves_execution_permission_instead_of_reinferring(self):
        executable = self.root / "script"
        executable.write_bytes(b"printf updated")
        executable.chmod(0o755)
        self.cli("add", "disabled", "old text")
        self.cli("update", "disabled", str(executable))
        self.assertFalse(self.metadata("disabled")["exec"])
        executable.chmod(0o644)
        self.cli("add", "enabled", "old text", "--exec")
        self.cli("update", "enabled", str(executable))
        self.assertTrue(self.metadata("enabled")["exec"])
        self.cli("update", "enabled", "replacement text")
        self.assertTrue(self.metadata("enabled")["exec"])

    def test_update_reads_redirected_stdin_and_empty_redirection_clears(self):
        self.cli("add", "note", "old")
        source = self.root / "replacement.txt"
        source.write_bytes(b"redirected bytes\n")
        for path, expected in ((source, b"redirected bytes\n"), (Path(os.devnull), b"")):
            with path.open("rb") as incoming:
                result = subprocess.run(
                    COMMAND + ["update", "note"], stdin=incoming,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    env=self.env, cwd=self.root, timeout=15,
                )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(self.cli("get", "note").stdout, expected)

    def test_multiple_objects_are_rejected_instead_of_joined(self):
        self.cli("add", "note", "two", "words", code=None)
        self.cli("get", "note", code=None)

    def test_flags_work_before_or_after_object_and_delimiter_preserves_literal(self):
        self.cli("add", "before", "--exec", "printf before")
        self.cli("add", "after", "printf after", "--exec")
        self.assertEqual(self.cli("run", "before").stdout, b"before")
        self.assertEqual(self.cli("run", "after").stdout, b"after")
        self.cli("update", "before", "--no-exec", "changed")
        self.assertFalse(self.metadata("before")["exec"])
        self.assertEqual(self.cli("get", "before").stdout, b"changed")
        self.cli("add", "option-looking", "--", "--exec")
        self.assertEqual(self.cli("get", "option-looking").stdout, b"--exec")
        self.assertFalse(self.metadata("option-looking")["exec"])

    def test_property_flags_can_precede_names_or_separate_positionals(self):
        placements = (
            ["--exec", "--mime", "text/x-shellscript", "NAME", "printf ready"],
            ["NAME", "--mime", "text/x-shellscript", "printf ready", "--exec"],
            ["NAME", "printf ready", "--exec", "--mime", "text/x-shellscript"],
        )
        for index, arguments in enumerate(placements):
            name = "layout_%d" % index
            arguments = [name if value == "NAME" else value for value in arguments]
            with self.subTest(arguments=arguments):
                self.cli("add", *arguments)
                self.assertEqual(self.cli("run", name).stdout, b"ready")
                self.assertEqual(self.metadata(name)["mime"], "text/x-shellscript")
                self.cli("update", *["--no-exec" if value == "--exec" else value
                                       for value in arguments])
                self.assertFalse(self.metadata(name)["exec"])
        for index, arguments in enumerate((
                ["--exec", "layout_0", "TARGET"],
                ["layout_0", "--exec", "TARGET"],
                ["layout_0", "TARGET", "--exec"])):
            name = "copy_%d" % index
            self.cli("copy", *[name if value == "TARGET" else value for value in arguments])
            self.assertEqual(self.cli("run", name).stdout, b"ready")

    def test_delimiter_preserves_option_looking_objects_on_add_and_update(self):
        for index, content in enumerate(("--exec", "--help", "--", "-value", "--mime=text/plain")):
            name = "literal_option_%d" % index
            with self.subTest(content=content):
                self.cli("add", "--binary", name, "--exec", "--", content)
                self.assertEqual(self.cli("get", name).stdout, content.encode())
                self.assertTrue(self.metadata(name)["exec"])
                self.assertFalse(self.metadata(name)["utf8"])
                self.cli("update", name, "--no-exec", "--", content)
                self.assertEqual(self.cli("get", name).stdout, content.encode())
                self.assertFalse(self.metadata(name)["exec"])
                self.cli("update", "--", name, content)
                self.assertEqual(self.cli("get", name).stdout, content.encode())

    def test_invalid_options_do_not_create_or_change_registers(self):
        for arguments in (("add", "note", "--ex", "printf nope"),
                          ("add",), ("add", "--exec", "--"),
                          ("add", "note", "--unknown"),
                          ("add", "note", "--exec", "--no-exec", "text"),
                          ("add", "note", "--mime")):
            with self.subTest(arguments=arguments):
                self.cli(*arguments, code=2)
                self.assertFalse(self.store.exists())
        self.cli("add", "note", "original")
        self.cli("update", "note", "--exec", "two", "objects", code=2)
        self.assertEqual(self.cli("get", "note").stdout, b"original")
        self.assertFalse(self.metadata("note")["exec"])

    def test_help_works_for_root_and_each_command_without_creating_store(self):
        for arguments in (("--help",), *((command, "--help") for command in (
                "add", "update", "copy", "remove", "show", "get", "run"))):
            with self.subTest(arguments=arguments):
                self.assertIn(b"usage:", self.cli(*arguments).stdout)
                self.assertFalse(self.store.exists())

    def test_regular_file_symlink_uses_target_contents_and_permissions(self):
        target = self.root / "target"
        target.write_bytes(b"printf linked")
        target.chmod(0o644)
        link = self.root / "link"
        link.symlink_to(target)
        self.cli("add", "linked", str(link))
        self.assertEqual(self.cli("get", "linked").stdout, b"printf linked")
        self.assertFalse(self.metadata("linked")["exec"])
        target.chmod(0o755)
        self.cli("add", "runnable", str(link))
        self.assertTrue(self.metadata("runnable")["exec"])
        self.assertEqual(self.cli("run", "runnable").stdout, b"linked")

    def test_get_rejects_arbitrary_destinations_and_primary(self):
        self.cli("add", "note", "precious")
        destination = self.root / "output.txt"
        for target in (str(destination), "PRIMARY", "-"):
            with self.subTest(target=target):
                self.cli("get", "note", target, code=None)
        self.assertFalse(destination.exists())
        self.assertEqual(self.cli("get", "note").stdout, b"precious")

    def test_binary_get_preserves_every_byte(self):
        content = bytes(range(256)) * 8
        self.cli("add", "blob", data=content)
        self.assertFalse(self.metadata("blob")["utf8"])
        self.assertEqual(self.cli("get", "blob").stdout, content)

    def test_pdf_detected_without_binary_flag(self):
        source = self.root / "report.pdf"
        content = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
        source.write_bytes(content)
        self.cli("add", "report", str(source))
        meta = self.metadata("report")
        self.assertFalse(meta["utf8"])
        self.assertEqual(meta["mime"], "application/pdf")
        self.assertEqual(self.cli("get", "report").stdout, content)

    def test_image_detected_from_piped_contents(self):
        self.cli("add", "picture", data=PNG)
        meta = self.metadata("picture")
        self.assertFalse(meta["utf8"])
        self.assertEqual(meta["mime"], "image/png")
        self.assertEqual(self.cli("get", "picture").stdout, PNG)

    def test_utf8_override_is_advisory_and_bytes_unchanged(self):
        self.cli("add", "blob", "--utf8", data=b"\xff\x00")
        self.assertTrue(self.metadata("blob")["utf8"])
        self.assertEqual(self.cli("get", "blob").stdout, b"\xff\x00")
        self.cli("update", "blob", "--binary", data=b"plain text")
        self.assertFalse(self.metadata("blob")["utf8"])
        self.assertEqual(self.cli("get", "blob").stdout, b"plain text")

    def test_content_replacement_redetects_type(self):
        self.cli("add", "buffer", data=PNG)
        self.cli("update", "buffer", data=b"now text")
        meta = self.metadata("buffer")
        self.assertTrue(meta["utf8"])
        self.assertEqual(meta["mime"].split(";", 1)[0], "text/plain")

    def test_explicit_mime_is_retained_without_changing_bytes(self):
        self.cli("add", "payload", "--mime", "application/example", data=b"payload")
        self.assertEqual(self.metadata("payload")["mime"], "application/example")
        self.assertEqual(self.cli("get", "payload").stdout, b"payload")
        for invalid in ("text/plain\nx-injected", "text /plain", "plain"):
            with self.subTest(mime=invalid):
                self.cli("update", "payload", "--mime", invalid, code=None)
        self.assertEqual(self.metadata("payload")["mime"], "application/example")

    def test_terminal_flag_updates_preserve_contents_and_unspecified_properties(self):
        content = b'printf "%s\\n" kept\n'
        self.cli("add", "task", "--exec", "--mime", "application/example", data=content)
        before = self.metadata("task")
        self.cli_tty("update", "task", "--no-exec")
        self.assertEqual(self.cli("get", "task").stdout, content)
        self.assertEqual(self.metadata("task"), {**before, "exec": False})
        self.cli_tty("update", "task", "--binary")
        self.assertEqual(self.cli("get", "task").stdout, content)
        self.assertEqual(self.metadata("task"), {**before, "exec": False, "utf8": False})

    def test_terminal_add_is_empty_and_terminal_update_preserves_all_properties(self):
        self.cli_tty("add", "blank")
        self.assertEqual(self.cli("get", "blank").stdout, b"")
        content = b"unchanged"
        self.cli("add", "note", "--exec", "--binary", "--mime", "application/example", data=content)
        before = self.metadata("note")
        self.cli_tty("update", "note")
        self.assertEqual(self.cli("get", "note").stdout, content)
        self.assertEqual(self.metadata("note"), before)

    def test_empty_stdin_with_flags_still_replaces_content(self):
        self.cli("add", "note", "--exec", data=b"old contents")
        self.cli("update", "note", "--no-exec", data=b"")
        self.assertEqual(self.cli("get", "note").stdout, b"")
        self.assertFalse(self.metadata("note")["exec"])

    def test_removed_input_options_fail_without_mutating_content(self):
        self.cli("add", "note", data=b"unchanged")
        for option, value in (("--source", "-"), ("--keep-content", None),
                              ("--text", "literal")):
            with self.subTest(option=option):
                args = ["update", "note", option]
                if value is not None:
                    args.append(value)
                self.cli(*args, data=b"replacement", code=None)
                self.assertEqual(self.cli("get", "note").stdout, b"unchanged")
        self.cli("get", "note", "--to", "CLIPBOARD", code=None)

    def test_add_refuses_existing_name_and_preserves_properties(self):
        self.cli("add", "note", "--exec", data=b"old")
        before = self.metadata("note")
        self.cli("add", "note", "--binary", data=b"replacement", code=None)
        self.assertEqual(self.cli("get", "note").stdout, b"old")
        self.assertEqual(self.metadata("note"), before)

    def test_copy_replaces_contents_and_all_properties(self):
        self.cli("add", "source", "--exec", data=PNG)
        self.cli("add", "target", data=b"old text")
        self.cli("copy", "source", "target")
        source = self.metadata("source")
        target = self.metadata("target")
        for key in ("exec", "utf8", "mime", "size"):
            self.assertEqual(target[key], source[key], key)
        self.assertEqual(target["name"], "target")
        self.assertEqual(self.cli("get", "target").stdout, PNG)
        self.cli("copy", "source", "new_target")
        self.assertEqual(self.cli("get", "new_target").stdout, PNG)

    def test_show_lists_sorted_names_and_json_properties(self):
        self.assertEqual(json.loads(self.cli("show").stdout), [])
        self.cli("add", "zebra", data=b"z")
        self.cli("add", "alpha", data=b"a")
        entries = json.loads(self.cli("show").stdout)
        self.assertEqual([entry["name"] for entry in entries], ["alpha", "zebra"])
        for entry in entries:
            self.assertTrue({"name", "exec", "utf8", "mime", "size"} <= entry.keys())
        selected = json.loads(self.cli("show", "zebra", "alpha").stdout)
        self.assertEqual({entry["name"] for entry in selected}, {"alpha", "zebra"})

    def test_unknown_names_fail_without_creating_registers(self):
        for args in (("get", "missing"), ("run", "missing"),
                     ("show", "missing"), ("remove", "missing"),
                     ("update", "missing"), ("copy", "missing", "new")):
            with self.subTest(args=args):
                self.cli(*args, data=b"data", code=None)
        self.assertEqual(json.loads(self.cli("show").stdout), [])

    def test_remove_and_name_reuse(self):
        self.cli("add", "note", data=b"old")
        self.cli("remove", "note")
        self.cli("get", "note", code=None)
        self.cli("add", "note", data=b"new")
        self.assertEqual(self.cli("get", "note").stdout, b"new")

    def test_reserved_endpoints_and_paths_are_rejected(self):
        for name in ("CLIPBOARD", ".", "..", "../escape", "a/b", ""):
            with self.subTest(name=name):
                self.cli("add", name, data=b"data", code=None)
        self.assertEqual(json.loads(self.cli("show").stdout), [])
        self.assertFalse((self.root / "escape").exists())

    def test_old_internal_filenames_are_safe_symbolic_names(self):
        for name in ("register_metadata.json", "metadata.lock", "register.sqlite3"):
            with self.subTest(name=name):
                self.cli("add", name, data=name.encode())
                self.assertEqual(self.cli("get", name).stdout, name.encode())

    def test_text_command_requires_execution_permission(self):
        self.cli("add", "task", data=b"printf ran")
        result = self.cli("run", "task", code=None)
        self.assertEqual(result.stdout, b"")
        self.cli_tty("update", "task", "--exec")
        self.assertEqual(self.cli("run", "task").stdout, b"ran")
        self.cli_tty("update", "task", "--no-exec")
        self.cli("run", "task", code=None)

    def test_no_shebang_uses_bash_even_if_shell_is_elsewhere(self):
        script = b'items=(alpha beta); [[ ${items[1]} == beta ]] && printf bash\n'
        self.cli("add", "task", "--exec", data=script)
        env = self.env.copy()
        env["SHELL"] = "/bin/false"
        self.assertEqual(self.cli("run", "task", env=env).stdout, b"bash")

    def test_shebang_selects_interpreter(self):
        self.cli("add", "task", "--exec", data=b"#!/usr/bin/env python3\nprint(6 * 7)\n")
        self.assertEqual(self.cli("run", "task").stdout, b"42\n")

    def test_run_preserves_caller_directory_and_argument_boundaries(self):
        caller = self.root / "directory with spaces"
        caller.mkdir()
        script = b'#!/bin/bash\nprintf "%s\\n" "$PWD" "$@"\n'
        self.cli("add", "task", "--exec", data=script)
        result = self.cli("run", "task", "two words", "--literal", "", cwd=caller)
        self.assertEqual(result.stdout, (str(caller) + "\ntwo words\n--literal\n\n").encode())

    def test_run_passes_help_options_and_double_dash_to_child_unchanged(self):
        self.cli("add", "arguments", 'printf "%s\\0" "$@"', "--exec")
        for arguments in (("--help",), ("--", "--help"),
                          ("--exec", "two words", "", "--", "--mime=text/plain")):
            with self.subTest(arguments=arguments):
                expected = b"".join(value.encode() + b"\0" for value in arguments)
                self.assertEqual(self.cli("run", "arguments", *arguments).stdout, expected)
                self.assertEqual(self.cli("run", "--", "arguments", *arguments).stdout, expected)

    def test_run_accepts_piped_input_and_output_can_be_captured(self):
        self.cli("add", "clean", "--exec", data=b"sed '/^[[:space:]]*$/d' | sort -u\n")
        producer = subprocess.Popen(COMMAND + ["run", "clean"], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=self.env, cwd=self.root)
        consumer = subprocess.Popen(COMMAND + ["add", "result"], stdin=producer.stdout,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=self.env, cwd=self.root)
        producer.stdout.close()
        _, producer_err = producer.communicate(b"pear\napple\npear\n\n", timeout=15)
        consumer_out, consumer_err = consumer.communicate(timeout=15)
        self.assertEqual(producer.returncode, 0, producer_err.decode())
        self.assertEqual(consumer.returncode, 0, consumer_err.decode())
        self.assertEqual(consumer_out, b"")
        self.assertEqual(self.cli("get", "result").stdout, b"apple\npear\n")

    def test_program_stdout_stderr_and_exit_status_propagate(self):
        self.cli("add", "task", "--exec", data=b"printf output; printf error >&2; exit 7\n")
        result = self.cli("run", "task", code=7)
        self.assertEqual(result.stdout, b"output")
        self.assertEqual(result.stderr, b"error")

    def test_signal_exit_status_is_128_plus_signal(self):
        self.cli("add", "task", "--exec", data=b"kill -TERM $$\n")
        self.cli("run", "task", code=143)

    def test_native_executable(self):
        binary = shutil.which("printf")
        if not binary:
            self.skipTest("native printf executable unavailable")
        self.cli("add", "native", "--exec", binary)
        self.assertEqual(self.cli("run", "native", "%s", "hello").stdout, b"hello")

    def test_invalid_file_sources_preserve_bytes_and_properties(self):
        directory = self.root / "directory"
        directory.mkdir()
        broken = self.root / "broken-link"
        broken.symlink_to(self.root / "absent-target")
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        self.cli("add", "note", "--exec", data=b"original")
        before = self.metadata("note")
        for source in (directory, broken, fifo):
            with self.subTest(source=source):
                result = self.cli("update", "note", "--no-exec", "--binary", str(source), code=1)
                reason = b"broken symlink" if source == broken else b"regular file"
                self.assertIn(reason, result.stderr)
                self.assertEqual(self.cli("get", "note").stdout, b"original")
                self.assertEqual(self.metadata("note"), before)

    def test_database_lock_failure_preserves_previous_complete_value(self):
        self.cli("add", "note", "--exec", data=b"original")
        before = self.metadata("note")
        connection = sqlite3.connect(self.store / "register.sqlite3")
        try:
            connection.execute("BEGIN EXCLUSIVE")
            result = self.cli("update", "note", "--no-exec", data=b"replacement", code=None)
            self.assertIn("locked", result.stderr.decode().lower())
        finally:
            connection.rollback()
            connection.close()
        self.assertEqual(self.cli("get", "note").stdout, b"original")
        self.assertEqual(self.metadata("note"), before)

    def test_retrieval_to_closed_pipe_has_no_python_traceback(self):
        self.cli("add", "large", data=b"x" * 262144)
        process = subprocess.Popen(COMMAND + ["get", "large"], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=self.env, cwd=self.root)
        process.stdout.close()
        error = process.stderr.read()
        process.stderr.close()
        process.wait(timeout=15)
        self.assertNotIn(b"Traceback", error)
        self.assertNotEqual(process.returncode, 0)


    def test_legacy_store_imports_bytes_and_flags(self):
        self.store.mkdir()
        (self.store / "note").write_bytes(b"legacy text")
        (self.store / "task").write_bytes(b"printf legacy")
        (self.store / "register_metadata.json").write_text(json.dumps({
            "note": {"exec": False, "utf8": False},
            "task": {"exec": True, "utf8": True},
        }))
        entries = json.loads(self.cli("show").stdout)
        self.assertEqual([entry["name"] for entry in entries], ["note", "task"])
        self.assertFalse(self.metadata("note")["utf8"])
        self.assertEqual(self.cli("get", "note").stdout, b"legacy text")
        self.assertEqual(self.cli("run", "task").stdout, b"legacy")
        for name in ("note", "task", "register_metadata.json"):
            self.assertFalse((self.store / name).exists(), name)

    def test_bad_legacy_metadata_leaves_originals_untouched(self):
        self.store.mkdir()
        (self.store / "note").write_bytes(b"precious")
        metadata = b"{malformed metadata"
        (self.store / "register_metadata.json").write_bytes(metadata)
        self.cli("show", code=None)
        self.assertEqual((self.store / "note").read_bytes(), b"precious")
        self.assertEqual((self.store / "register_metadata.json").read_bytes(), metadata)

    def test_incomplete_legacy_store_is_not_partially_imported(self):
        self.store.mkdir()
        (self.store / "valid").write_bytes(b"precious")
        metadata = json.dumps({"valid": {"exec": False, "utf8": True},
                               "missing": {"exec": False, "utf8": True}}).encode()
        (self.store / "register_metadata.json").write_bytes(metadata)
        self.cli("show", code=None)
        self.assertEqual((self.store / "valid").read_bytes(), b"precious")
        self.assertEqual((self.store / "register_metadata.json").read_bytes(), metadata)

    def test_legacy_buffer_named_like_new_database_is_preserved(self):
        self.store.mkdir()
        (self.store / "register.sqlite3").write_bytes(b"old buffer contents")
        (self.store / "register_metadata.json").write_text(json.dumps({
            "register.sqlite3": {"exec": False, "utf8": True},
        }))
        self.assertEqual(self.cli("get", "register.sqlite3").stdout, b"old buffer contents")
        self.assertFalse((self.store / "register_metadata.json").exists())

    def test_unrecognized_database_file_is_not_overwritten(self):
        self.store.mkdir()
        path = self.store / "register.sqlite3"
        path.write_bytes(b"unrelated file")
        self.cli("show", code=None)
        self.assertEqual(path.read_bytes(), b"unrelated file")

    def test_concurrent_first_use_creates_one_complete_store(self):
        processes = []
        try:
            for index in range(6):
                process = subprocess.Popen(
                    COMMAND + ["add", "note_%d" % index],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    env=self.env, cwd=self.root,
                )
                process.stdin.write(("value_%d" % index).encode())
                process.stdin.close()
                process.stdin = None
                processes.append(process)
            for process in processes:
                output, error = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, error.decode())
                self.assertEqual(output, b"")
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        self.assertEqual(len(json.loads(self.cli("show").stdout)), 6)
        for index in range(6):
            self.assertEqual(self.cli("get", "note_%d" % index).stdout,
                             ("value_%d" % index).encode())


FAKE_DESKTOP = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

root = Path(os.environ["FAKE_DESKTOP_HOME"])
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with (root / "commands.jsonl").open("a") as log:
    log.write(json.dumps([name] + args) + "\n")
if os.environ.get("FAKE_DESKTOP_FAIL"):
    print("simulated clipboard failure", file=sys.stderr)
    sys.exit(3)

def option(flag, default=None):
    return args[args.index(flag) + 1] if flag in args else default

if "--primary" in args or (name == "xclip" and option("-selection") != "clipboard"):
    print("unexpected selection", file=sys.stderr)
    sys.exit(6)
meta_path = root / "clipboard.json"
data_path = root / "clipboard.bin"
meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"types": []}
if name == "wl-paste" or (name == "xclip" and "-out" in args):
    requested = option("-target") if name == "xclip" else option("--type")
    if "--list-types" in args or requested == "TARGETS":
        print("\n".join(meta["types"]))
    else:
        if requested not in meta["types"]:
            print("requested unavailable type " + str(requested), file=sys.stderr)
            sys.exit(4)
        sys.stdout.buffer.write(data_path.read_bytes())
elif name == "wl-copy" or (name == "xclip" and "-in" in args):
    requested = option("-target") if name == "xclip" else option("--type")
    data_path.write_bytes(sys.stdin.buffer.read())
    meta_path.write_text(json.dumps({"types": [requested], "written_type": requested}))
    if os.environ.get("FAKE_DESKTOP_BACKGROUND"):
        import time
        child = os.fork()
        if child == 0:
            (root / "owner.pid").write_text(str(os.getpid()))
            time.sleep(30)
            os._exit(0)
else:
    print("unknown clipboard invocation", file=sys.stderr)
    sys.exit(5)
'''


class ClipboardCLI(CLIBase):
    def setUp(self):
        super().setUp()
        self.desktop = self.root / "desktop"
        self.desktop.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        # An absolute shebang also works when PATH intentionally has no helpers.
        source = FAKE_DESKTOP.replace("#!/usr/bin/env python3", "#!" + sys.executable, 1)
        for name in ("xclip", "wl-copy", "wl-paste"):
            path = self.bin / name
            path.write_text(source)
            path.chmod(0o755)
        self.env["PATH"] = str(self.bin) + os.pathsep + self.env.get("PATH", "")
        self.env["FAKE_DESKTOP_HOME"] = str(self.desktop)

    def backend(self, kind):
        env = self.env.copy()
        if kind == "x11":
            env["DISPLAY"] = ":99"
            env["XDG_SESSION_TYPE"] = "x11"
        else:
            env["WAYLAND_DISPLAY"] = "wayland-test"
            env["XDG_SESSION_TYPE"] = "wayland"
            # A Wayland desktop often advertises an XWayland DISPLAY too.
            env["DISPLAY"] = ":99"
        return env

    def seed(self, content, types):
        (self.desktop / "clipboard.bin").write_bytes(content)
        (self.desktop / "clipboard.json").write_text(json.dumps({"types": types}))

    def written(self):
        meta = json.loads((self.desktop / "clipboard.json").read_text())
        return (self.desktop / "clipboard.bin").read_bytes(), meta["written_type"]

    def test_identical_clipboard_api_preserves_text_on_each_desktop(self):
        content = "café\nno added newline".encode()
        for kind in ("x11", "wayland"):
            with self.subTest(backend=kind):
                env = self.backend(kind)
                types = ["UTF8_STRING", "text/plain"] if kind == "x11" else ["text/plain;charset=utf-8"]
                self.seed(content, types)
                name = kind + "_note"
                self.cli("add", name, "CLIPBOARD", env=env)
                self.assertEqual(self.cli("get", name).stdout, content)
                self.seed(b"different", types)
                self.cli("get", name, "CLIPBOARD", env=env)
                self.assertEqual(self.written()[0], content)

    def test_image_clipboard_round_trip_preserves_mime_and_bytes(self):
        for kind in ("x11", "wayland"):
            with self.subTest(backend=kind):
                env = self.backend(kind)
                # A copied image can also advertise text. Prefer the image.
                types = ["text/plain", "image/png"]
                self.seed(PNG, types)
                name = kind + "_image"
                self.cli("add", name, "CLIPBOARD", env=env)
                self.assertEqual(self.metadata(name)["mime"], "image/png")
                self.assertEqual(self.cli("get", name).stdout, PNG)
                self.seed(b"different", ["text/plain"])
                self.cli("get", name, "CLIPBOARD", env=env)
                self.assertEqual(self.written(), (PNG, "image/png"))

    def test_clipboard_token_wins_over_a_file_with_the_same_name(self):
        (self.root / "CLIPBOARD").write_bytes(b"file contents")
        for kind in ("x11", "wayland"):
            with self.subTest(backend=kind):
                env = self.backend(kind)
                types = ["UTF8_STRING"] if kind == "x11" else ["text/plain"]
                self.seed(b"clipboard contents", types)
                name = kind + "_reserved_input"
                self.cli("add", name, "CLIPBOARD", env=env)
                self.assertEqual(self.cli("get", name).stdout, b"clipboard contents")
                self.assertEqual((self.root / "CLIPBOARD").read_bytes(), b"file contents")

    def test_empty_text_clipboard_is_valid_and_clears(self):
        for kind in ("x11", "wayland"):
            with self.subTest(backend=kind):
                env = self.backend(kind)
                types = ["UTF8_STRING"] if kind == "x11" else ["text/plain"]
                self.seed(b"", types)
                name = kind + "_empty"
                self.cli("add", name, data=b"old")
                self.cli("update", name, "CLIPBOARD", env=env)
                self.assertEqual(self.cli("get", name).stdout, b"")
                self.cli("get", name, "CLIPBOARD", env=env)
                self.assertEqual(self.written()[0], b"")

    def test_imported_image_can_be_sent_to_clipboard(self):
        self.cli("add", "picture", data=PNG)
        for kind in ("x11", "wayland"):
            with self.subTest(backend=kind):
                self.cli("get", "picture", "CLIPBOARD", env=self.backend(kind))
                self.assertEqual(self.written(), (PNG, "image/png"))

    def test_failed_clipboard_read_preserves_previous_register(self):
        self.cli("add", "note", "--exec", data=b"original")
        before = self.metadata("note")
        env = self.backend("wayland")
        env["FAKE_DESKTOP_FAIL"] = "1"
        self.cli("update", "note", "--no-exec", "CLIPBOARD", env=env, code=None)
        self.assertEqual(self.cli("get", "note").stdout, b"original")
        self.assertEqual(self.metadata("note"), before)

    def test_wayland_is_preferred_with_both_desktop_environment_variables(self):
        self.seed(b"hello", ["text/plain"])
        self.cli("add", "note", "CLIPBOARD", env=self.backend("wayland"))
        commands = [json.loads(line) for line in (self.desktop / "commands.jsonl").read_text().splitlines()]
        self.assertTrue(commands)
        self.assertTrue(all(command[0] == "wl-paste" for command in commands), commands)
        reads = [command for command in commands if "--type" in command]
        self.assertTrue(reads)
        self.assertTrue(all("--no-newline" in command for command in reads))

    def test_missing_helpers_or_desktop_fail_cleanly(self):
        self.cli("add", "note", data=b"saved")
        env = self.backend("x11")
        empty_path = self.root / "empty_path"
        empty_path.mkdir()
        env["PATH"] = str(empty_path)
        self.cli("get", "note", "CLIPBOARD", env=env, code=None)
        self.cli("get", "note", "CLIPBOARD", code=None)
        self.assertEqual(self.cli("get", "note").stdout, b"saved")

    def test_background_clipboard_owner_does_not_delay_command_completion(self):
        self.cli("add", "note", data=b"saved")
        env = self.backend("wayland")
        env["FAKE_DESKTOP_BACKGROUND"] = "1"
        started = time.monotonic()
        try:
            self.cli("get", "note", "CLIPBOARD", env=env)
            self.assertLess(time.monotonic() - started, 3)
            self.assertEqual(self.written()[0], b"saved")
        finally:
            # The fake owner deliberately keeps inherited stdout/stderr open;
            # clean it up even when a broken implementation times out.
            pid_path = self.desktop / "owner.pid"
            deadline = time.monotonic() + 1
            while not pid_path.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            if pid_path.exists():
                try:
                    os.kill(int(pid_path.read_text()), signal.SIGTERM)
                except ProcessLookupError:
                    pass


if __name__ == "__main__":
    unittest.main()
