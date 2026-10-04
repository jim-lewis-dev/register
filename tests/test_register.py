"""Behavior checks for the command line; every test uses its own empty store.

Clipboard checks use simulated desktop helpers. They verify Register's backend
commands, MIME choices, and byte preservation without needing a live desktop.
"""

import base64
import json
import os
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

    def test_explicit_stdin_source(self):
        self.cli("add", "note", "--source", "-", data=b"one")
        self.cli("update", "note", "--source", "-", data=b"two")
        self.assertEqual(self.cli("get", "note").stdout, b"two")

    def test_source_file_is_copied_not_linked(self):
        source = self.root / "filename with spaces.json"
        source.write_bytes(b'{"answer":42}\n')
        self.cli("add", "config", "--source", str(source))
        source.write_bytes(b"changed")
        source.unlink()
        self.assertEqual(self.cli("get", "config").stdout, b'{"answer":42}\n')

    def test_binary_get_preserves_every_byte(self):
        content = bytes(range(256)) * 8
        self.cli("add", "blob", data=content)
        self.assertFalse(self.metadata("blob")["utf8"])
        self.assertEqual(self.cli("get", "blob").stdout, content)

    def test_pdf_detected_without_binary_flag(self):
        source = self.root / "report.pdf"
        content = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
        source.write_bytes(content)
        self.cli("add", "report", "--source", str(source))
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

    def test_text_flag_is_advisory_and_bytes_unchanged(self):
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
                self.cli("update", "payload", "--keep-content", "--mime", invalid, code=None)
        self.assertEqual(self.metadata("payload")["mime"], "application/example")

    def test_keep_content_changes_properties_without_reading_input(self):
        content = b'printf "%s\\n" kept\n'
        self.cli("add", "task", "--exec", data=content)
        self.cli("update", "task", "--no-exec", "--keep-content", data=b"")
        self.assertEqual(self.cli("get", "task").stdout, content)
        self.assertFalse(self.metadata("task")["exec"])
        self.cli("update", "task", "--binary", "--keep-content", data=b"ignored")
        self.assertEqual(self.cli("get", "task").stdout, content)
        self.assertFalse(self.metadata("task")["utf8"])

    def test_keep_content_cannot_be_combined_with_source(self):
        self.cli("add", "note", data=b"unchanged")
        self.cli("update", "note", "--keep-content", "--source", "-", code=None)
        self.assertEqual(self.cli("get", "note").stdout, b"unchanged")

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
        for name in ("CLIPBOARD", "PRIMARY", ".", "..", "../escape", "a/b", ""):
            with self.subTest(name=name):
                self.cli("add", name, data=b"data", code=None)
        self.assertEqual(json.loads(self.cli("show").stdout), [])
        self.assertFalse((self.root / "escape").exists())

    def test_old_internal_filenames_are_safe_symbolic_names(self):
        for name in ("register_metadata.json", "metadata.lock", "register.sqlite3"):
            with self.subTest(name=name):
                self.cli("add", name, data=name.encode())
                self.assertEqual(self.cli("get", name).stdout, name.encode())

    def test_run_requires_explicit_execution_permission(self):
        self.cli("add", "task", data=b"printf ran")
        result = self.cli("run", "task", code=None)
        self.assertEqual(result.stdout, b"")
        self.cli("update", "task", "--exec", "--keep-content")
        self.assertEqual(self.cli("run", "task").stdout, b"ran")
        self.cli("update", "task", "--no-exec", "--keep-content")
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
        self.cli("add", "native", "--exec", "--source", binary)
        self.assertEqual(self.cli("run", "native", "%s", "hello").stdout, b"hello")

    def test_missing_update_source_preserves_bytes_and_properties(self):
        self.cli("add", "note", "--exec", data=b"original")
        before = self.metadata("note")
        self.cli("update", "note", "--no-exec", "--binary", "--source",
                 str(self.root / "missing"), code=None)
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

selection = option("-selection", "clipboard") if name == "xclip" else (
    "primary" if "--primary" in args else "clipboard")
meta_path = root / (selection + ".json")
data_path = root / (selection + ".bin")
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

    def seed(self, content, types, selection="clipboard"):
        (self.desktop / (selection + ".bin")).write_bytes(content)
        (self.desktop / (selection + ".json")).write_text(json.dumps({"types": types}))

    def written(self, selection="clipboard"):
        meta = json.loads((self.desktop / (selection + ".json")).read_text())
        return (self.desktop / (selection + ".bin")).read_bytes(), meta["written_type"]

    def test_identical_clipboard_api_preserves_text_on_each_desktop(self):
        content = "café\nno added newline".encode()
        for kind in ("x11", "wayland"):
            with self.subTest(backend=kind):
                env = self.backend(kind)
                types = ["UTF8_STRING", "text/plain"] if kind == "x11" else ["text/plain;charset=utf-8"]
                self.seed(content, types)
                name = kind + "_note"
                self.cli("add", name, "--source", "CLIPBOARD", env=env)
                self.assertEqual(self.cli("get", name).stdout, content)
                self.seed(b"different", types)
                self.cli("get", name, "--to", "CLIPBOARD", env=env)
                self.assertEqual(self.written()[0], content)

    def test_image_clipboard_round_trip_preserves_mime_and_bytes(self):
        for kind in ("x11", "wayland"):
            with self.subTest(backend=kind):
                env = self.backend(kind)
                # A copied image can also advertise text. Prefer the image.
                types = ["text/plain", "image/png"]
                self.seed(PNG, types)
                name = kind + "_image"
                self.cli("add", name, "--source", "CLIPBOARD", env=env)
                self.assertEqual(self.metadata(name)["mime"], "image/png")
                self.assertEqual(self.cli("get", name).stdout, PNG)
                self.seed(b"different", ["text/plain"])
                self.cli("get", name, "--to", "CLIPBOARD", env=env)
                self.assertEqual(self.written(), (PNG, "image/png"))

    def test_primary_uses_same_interface(self):
        for kind in ("x11", "wayland"):
            with self.subTest(backend=kind):
                env = self.backend(kind)
                types = ["UTF8_STRING"] if kind == "x11" else ["text/plain"]
                self.seed(b"selected", types, selection="primary")
                name = kind + "_selection"
                self.cli("add", name, "--source", "PRIMARY", env=env)
                self.assertEqual(self.cli("get", name).stdout, b"selected")
                self.cli("get", name, "--to", "PRIMARY", env=env)
                self.assertEqual(self.written("primary")[0], b"selected")

    def test_empty_text_clipboard_is_valid_and_clears(self):
        for kind in ("x11", "wayland"):
            with self.subTest(backend=kind):
                env = self.backend(kind)
                types = ["UTF8_STRING"] if kind == "x11" else ["text/plain"]
                self.seed(b"", types)
                name = kind + "_empty"
                self.cli("add", name, data=b"old")
                self.cli("update", name, "--source", "CLIPBOARD", env=env)
                self.assertEqual(self.cli("get", name).stdout, b"")
                self.cli("get", name, "--to", "CLIPBOARD", env=env)
                self.assertEqual(self.written()[0], b"")

    def test_imported_image_can_be_sent_to_clipboard(self):
        self.cli("add", "picture", data=PNG)
        for kind in ("x11", "wayland"):
            with self.subTest(backend=kind):
                self.cli("get", "picture", "--to", "CLIPBOARD", env=self.backend(kind))
                self.assertEqual(self.written(), (PNG, "image/png"))

    def test_failed_clipboard_read_preserves_previous_register(self):
        self.cli("add", "note", "--exec", data=b"original")
        before = self.metadata("note")
        env = self.backend("wayland")
        env["FAKE_DESKTOP_FAIL"] = "1"
        self.cli("update", "note", "--no-exec", "--source", "CLIPBOARD", env=env, code=None)
        self.assertEqual(self.cli("get", "note").stdout, b"original")
        self.assertEqual(self.metadata("note"), before)

    def test_wayland_is_preferred_with_both_desktop_environment_variables(self):
        self.seed(b"hello", ["text/plain"])
        self.cli("add", "note", "--source", "CLIPBOARD", env=self.backend("wayland"))
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
        self.cli("get", "note", "--to", "CLIPBOARD", env=env, code=None)
        self.cli("get", "note", "--to", "CLIPBOARD", code=None)
        self.assertEqual(self.cli("get", "note").stdout, b"saved")

    def test_background_clipboard_owner_does_not_delay_command_completion(self):
        self.cli("add", "note", data=b"saved")
        env = self.backend("wayland")
        env["FAKE_DESKTOP_BACKGROUND"] = "1"
        started = time.monotonic()
        try:
            self.cli("get", "note", "--to", "CLIPBOARD", env=env)
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
