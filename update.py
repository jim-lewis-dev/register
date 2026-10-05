#!/usr/bin/env python3
"""Apply a Register ZIP, test it, commit it, and remove the downloaded ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import zipfile


class UpdateError(Exception):
    pass


MAX_UNPACKED = 100 * 1024 * 1024


def _run(project, command, *, check=True, capture=True):
    result = subprocess.run(
        command, cwd=project, text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        env={**os.environ, "GIT_LITERAL_PATHSPECS": "1", "GIT_OPTIONAL_LOCKS": "0",
             "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise UpdateError("Command failed: " + " ".join(command)
                          + ("\n" + detail if detail else ""))
    return result


def _git(project, *args, check=True):
    return _run(project, ["git", *args], check=check)


def _safe_path(name):
    if not isinstance(name, str) or not name or "\0" in name or "\\" in name:
        raise UpdateError("Invalid archive path")
    path = PurePosixPath(name)
    if path.is_absolute() or str(path) != name or any(part in ("..", ".git", ".register")
                                                    for part in path.parts):
        raise UpdateError("Unsafe archive path: " + name)
    lower = path.name.casefold()
    if (lower == ".env" or lower.startswith(".env.")
            or lower in (".netrc", "credentials", "secrets")
            or re.search(r"\.(db|sqlite|sqlite3)($|[-.])", lower)):
        raise UpdateError("Archive contains a private or runtime file: " + name)
    return name


def _load_archive(archive):
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        names = [member.filename for member in members]
        if len(set(names)) != len(names):
            raise UpdateError("Archive contains duplicate members")
        if sum(member.file_size for member in members) > MAX_UNPACKED:
            raise UpdateError("Archive is too large")
        for member in members:
            mode = member.external_attr >> 16
            if member.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                raise UpdateError("Archive contains a directory or special file: " + member.filename)
        manifest = json.loads(bundle.read("manifest.json"))
        if not isinstance(manifest, dict) or manifest.get("kind") != "register-update":
            raise UpdateError("This is not a Register update ZIP")
        message = manifest.get("commit_message")
        if not isinstance(message, str) or not message.strip() or "\0" in message:
            raise UpdateError("Archive has no valid commit message")
        expected = {"manifest.json"}
        groups = {}
        for key in ("baseline", "files"):
            records = manifest.get(key)
            if not isinstance(records, list):
                raise UpdateError("Archive has no valid " + key + " list")
            entries = {}
            for record in records:
                if not isinstance(record, dict):
                    raise UpdateError("Invalid archive record")
                name = _safe_path(record.get("path"))
                mode = record.get("mode")
                checksum = record.get("sha256")
                if (type(mode) is not int or not 0 <= mode <= 0o777
                        or not isinstance(checksum, str)
                        or not re.fullmatch(r"[0-9a-f]{64}", checksum)):
                    raise UpdateError("Invalid checksum or mode: " + name)
                if name in entries:
                    raise UpdateError("Duplicate archive path: " + name)
                member = key + "/" + name
                data = bundle.read(member)
                if hashlib.sha256(data).hexdigest() != checksum:
                    raise UpdateError("Archive checksum mismatch: " + name)
                if data.startswith(b"SQLite format 3\0"):
                    raise UpdateError("Archive contains a runtime database: " + name)
                entries[name] = (data, mode)
                expected.add(member)
            groups[key] = entries
        original, replacement = groups["baseline"], groups["files"]
        if not replacement or "update.py" not in replacement:
            raise UpdateError("Archive has no project files or update.py")
        removed = manifest.get("remove")
        if not isinstance(removed, list):
            raise UpdateError("Archive has no valid removal list")
        removed = [_safe_path(name) for name in removed]
        if (len(set(removed)) != len(removed) or set(removed) & set(replacement)
                or not set(removed) <= set(original)):
            raise UpdateError("Archive has conflicting or unreferenced removals")
        project_names = set(original) | set(replacement)
        if any(str(parent) in project_names for name in project_names
               for parent in PurePosixPath(name).parents):
            raise UpdateError("Archive treats a file as a directory")
        if "update.py" in names:
            if bundle.read("update.py") != replacement["update.py"][0]:
                raise UpdateError("Bootstrap update.py differs from the project copy")
            expected.add("update.py")
        if set(names) != expected:
            raise UpdateError("Archive contains missing or undeclared files")
    return original, replacement, removed, message


def _regular_or_absent(project, name):
    path = project / name
    for current in [path, *path.parents]:
        if current == project:
            break
        if current.is_symlink():
            raise UpdateError("Refusing to write through a symlink: " + str(current))
        if current.exists() and not (current.is_file() if current == path else current.is_dir()):
            raise UpdateError("Unexpected project path: " + str(current))
    return path


def _read_file(path):
    if not path.exists():
        return None
    return path.read_bytes(), stat.S_IMODE(path.stat().st_mode)


def _atomic_write(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".register-update-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _head(project):
    return _git(project, "rev-parse", "--verify", "HEAD").stdout.strip()


def _preflight(project, original, replacement, removed):
    top = _git(project, "rev-parse", "--show-toplevel", check=False)
    if top.returncode or Path(top.stdout.strip()).resolve() != project:
        raise UpdateError("The project must be the root of an existing Git repository")
    _head(project)
    gitdir = Path(_git(project, "rev-parse", "--absolute-git-dir").stdout.strip())
    for state in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply"):
        if (gitdir / state).exists():
            raise UpdateError("Finish or abort the current Git operation first")
    status = _git(project, "status", "--porcelain", "--untracked-files=all").stdout
    if status:
        raise UpdateError("Commit or remove uncommitted changes first, including untracked files."
                          "\nNo project files were changed.\n" + status.rstrip())
    for kind in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
        if _git(project, "var", kind, check=False).returncode:
            raise UpdateError("Set Git user.name and user.email before updating")
    current = {}
    for name in set(original) | set(replacement):
        value = _read_file(_regular_or_absent(project, name))
        current[name] = value
        if name in original:
            if value != original[name]:
                raise UpdateError("Current file differs from the supplied snapshot: " + name
                                  + "\nExport the current project before overwriting it.")
        elif value is not None and (value[0] != replacement[name][0]
                                   or (name != "update.py" and value[1] != replacement[name][1])):
            raise UpdateError("New project file already contains other work: " + name)
    tests = [name for name in replacement if name.startswith("tests/test_") and name.endswith(".py")]
    if not tests:
        raise UpdateError("Archive has no project test suite")
    touched = list(dict.fromkeys([*replacement, *removed]))
    return gitdir, {name: current[name] for name in touched}


def _archive_identity(path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _inside(path, directory):
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def _newest_archive(downloads):
    candidates = []
    for path in downloads.expanduser().glob("register_update*.zip"):
        if re.fullmatch(r"register_update(?: ?\([0-9]+\))?\.zip", path.name) and path.is_file():
            candidates.append(path)
    if not candidates:
        raise UpdateError("No register_update ZIP found in " + str(downloads.expanduser()))
    return max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name))


def apply_update(project, archive, downloads):
    project = Path(project).expanduser().absolute()
    if project.is_symlink() or not project.is_dir():
        raise UpdateError("Project directory must exist and must not be a symlink")
    project = project.resolve()
    archive = Path(archive).expanduser().resolve(strict=True)
    downloads = Path(downloads).expanduser().resolve()
    identity = _archive_identity(archive)
    original, replacement, removed, message = _load_archive(archive)
    gitdir, previous = _preflight(project, original, replacement, removed)
    lock = gitdir / "register-update.lock"
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise UpdateError("Another Register update is running (" + str(lock) + ")") from error
    os.close(fd)
    try:
        old_head = _head(project)
        index = Path(_git(project, "rev-parse", "--git-path", "index").stdout.strip())
        if not index.is_absolute():
            index = project / index
        old_index = _read_file(index)
        written = []
        created_dirs = set()
        try:
            for name, value in replacement.items():
                target = _regular_or_absent(project, name)
                if _read_file(target) != previous[name]:
                    raise UpdateError("Project file changed during this update: " + name)
                for parent in target.parents:
                    if parent == project or parent.exists():
                        break
                    created_dirs.add(parent)
                _atomic_write(target, *value)
                written.append(name)
            for name in removed:
                target = _regular_or_absent(project, name)
                if _read_file(target) != previous[name]:
                    raise UpdateError("Project file changed during this update: " + name)
                target.unlink()
                written.append(name)
            print("Applied Register files. Running tests...", flush=True)
            result = _run(project, [sys.executable, "-m", "unittest", "discover", "-s", "tests"], check=False)
            if result.stdout:
                print(result.stdout, end="", flush=True)
            if result.stderr:
                print(result.stderr, end="", file=sys.stderr, flush=True)
            if result.returncode:
                raise UpdateError("Tests failed; the update was not committed")
            if _head(project) != old_head:
                raise UpdateError("Git HEAD changed during the update")
            _git(project, "add", "--", *previous)
            if _git(project, "diff", "--cached", "--quiet", check=False).returncode == 0:
                raise UpdateError("This ZIP makes no changes to commit")
            _git(project, "commit", "-m", message)
        except BaseException as error:
            if _head(project) == old_head:
                for name in reversed(written):
                    if previous[name] is None:
                        (project / name).unlink(missing_ok=True)
                    else:
                        _atomic_write(project / name, *previous[name])
                if old_index is None:
                    index.unlink(missing_ok=True)
                else:
                    _atomic_write(index, *old_index)
                for directory in sorted(created_dirs, key=lambda path: len(path.parts), reverse=True):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
                raise
            raise UpdateError("Git HEAD changed. The updater kept that commit and left the working tree"
                              " for inspection; the ZIP was kept.\n" + str(error)) from error
        print("Tests passed. Committed: " + message, flush=True)
        if _inside(archive, downloads):
            try:
                if _archive_identity(archive) != identity:
                    print("The ZIP changed while updating; it was kept.", flush=True)
                else:
                    archive.unlink()
                    print("Deleted the downloaded ZIP.", flush=True)
            except OSError as error:
                print("The update is committed, but ZIP cleanup could not finish: " + str(error),
                      file=sys.stderr, flush=True)
        else:
            print("The ZIP is outside Downloads; it was kept.", flush=True)
        return 0
    finally:
        lock.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", nargs="?", type=Path,
                        help="ZIP to apply (default: newest register_update ZIP in Downloads)")
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parent,
                        help="project directory (default: directory containing update.py)")
    parser.add_argument("--downloads", type=Path, default=Path.home() / "Downloads",
                        help="Downloads directory")
    args = parser.parse_args(argv)
    try:
        archive = args.archive if args.archive is not None else _newest_archive(args.downloads)
        print("Applying: " + str(archive.expanduser().absolute()), flush=True)
        return apply_update(args.project, archive, args.downloads)
    except (UpdateError, OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        print("Register update failed: " + str(error), file=sys.stderr)
        print("The ZIP was kept. No installer or shell setup was run.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
