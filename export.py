#!/usr/bin/env python3
"""Export current Register source to a project-local snapshot, with or without Git.

    python3 export.py
    python3 export.py --project ~/projects/register
    python3 export.py --project ~/projects/register --output ~/Downloads/register-snapshot.zip

The default project is the directory containing this script. Relative --output
paths are relative to the project. Git repositories use tracked files plus
nonignored untracked files; other folders use a filtered directory traversal.
The exporter never initializes Git, commits changes, or edits source files.
"""

import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile


ARCHIVE_PREFIX = "register/"
DEFAULT_OUTPUT = "register-snapshot.zip"
PROVENANCE = "SNAPSHOT.txt"
EXCLUDED_DIRECTORIES = {
    ".git", ".hg", ".svn", ".register", ".venv", "venv", "virtualenv",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    ".nox", ".cache", "node_modules", ".next", ".nuxt", ".ssh", ".aws",
    ".azure", ".gnupg", ".kube", "secrets",
}
EXCLUDED_FILES = {
    "register-snapshot.zip", "register_snapshot.zip", "register-update.zip",
    "register_update.zip", "register_metadata.json", "register_metadata.json.tmp",
    "metadata.lock", ".netrc", ".npmrc", ".pypirc", ".ds_store", ".coverage",
    "credentials.json", "credentials.yml", "credentials.yaml", "credentials.toml",
    "secrets.json", "secrets.yml", "secrets.yaml", "secrets.toml", ".secrets",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}
DATABASE_SUFFIXES = (".db", ".sqlite", ".sqlite3")


class ExportError(Exception):
    pass


def fingerprint(details):
    return (details.st_dev, details.st_ino, details.st_mode, details.st_size,
            details.st_mtime_ns, details.st_ctime_ns)


def quoted(path):
    return json.dumps(path, ensure_ascii=True)


class Exporter:
    def __init__(self, project, output):
        self.project = project.resolve()
        self.output = output
        self.content_exclusions = set()
        self.git_program = shutil.which("git")
        self.git_backed = self.detect_git()

    def git(self, *arguments, optional=False):
        result = subprocess.run(
            [self.git_program, "--no-optional-locks", "-C", str(self.project), *arguments],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if result.returncode:
            if optional:
                return None
            raise ExportError(result.stderr.decode("utf-8", "replace").strip() or "Git command failed")
        return result.stdout

    def detect_git(self):
        # A containing repository does not make this project a repository root.
        if not os.path.lexists(self.project / ".git"):
            return False
        if self.git_program is None:
            raise ExportError("this project has Git metadata, but Git is not installed")
        root = self.git("rev-parse", "--show-toplevel").decode("utf-8", "surrogateescape").rstrip("\n")
        if Path(root).resolve() != self.project:
            raise ExportError("the project's Git metadata points to a different working-tree root")
        return True

    def excluded(self, name):
        path = Path(name)
        filename = path.name.lower()
        if self.project / name == self.output:
            return True
        if any(part.lower() in EXCLUDED_DIRECTORIES for part in path.parts):
            return True
        if filename in EXCLUDED_FILES or filename.startswith(".register-export-"):
            return True
        if filename == ".env" or filename.startswith(".env."):
            return True
        if path.suffix.lower() in EXCLUDED_SUFFIXES:
            return True
        database_name = filename
        for tail in ("-journal", "-wal", "-shm"):
            if database_name.endswith(tail):
                database_name = database_name[:-len(tail)]
                break
        return database_name.endswith(DATABASE_SUFFIXES)

    def source_names(self):
        if self.git_backed:
            paths = self.git("ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0")
            return sorted({os.fsdecode(path) for path in paths if path and not self.excluded(os.fsdecode(path))})
        names = []

        def traversal_error(error):
            raise error

        for directory, subdirectories, files in os.walk(self.project, followlinks=False, onerror=traversal_error):
            relative = Path(directory).relative_to(self.project)
            retained = []
            for name in sorted(subdirectories):
                candidate = relative / name
                if self.excluded(candidate.as_posix()):
                    continue
                if (self.project / candidate).is_symlink():
                    names.append(candidate.as_posix())
                else:
                    retained.append(name)
            subdirectories[:] = retained
            for name in files:
                candidate = (relative / name).as_posix()
                if not self.excluded(candidate):
                    names.append(candidate)
        return sorted(names)

    def source_status(self):
        fields = self.git("status", "--porcelain=v1", "--untracked-files=all", "-z").split(b"\0")
        entries = []
        index = 0
        while index < len(fields) and fields[index]:
            value = fields[index]
            index += 1
            code, name = value[:2].decode("ascii"), os.fsdecode(value[3:])
            previous = None
            if "R" in code or "C" in code:
                previous = os.fsdecode(fields[index])
                index += 1
            if self.excluded(name) or (previous is not None and self.excluded(previous)):
                continue
            entries.append((code, name, previous))
        return entries

    def provenance(self):
        if not self.git_backed:
            return {"git": False}
        head = self.git("rev-parse", "--verify", "HEAD", optional=True)
        branch = self.git("symbolic-ref", "--quiet", "--short", "HEAD", optional=True)
        commits = []
        if head is not None:
            fields = self.git("log", "-5", "--format=%H%x00%s", "-z").split(b"\0")
            for index in range(0, len(fields) - 1, 2):
                commits.append((fields[index].decode("ascii"), fields[index + 1].decode("utf-8", "replace")))
        return {"git": True, "head": head.decode().strip() if head else None,
                "branch": branch.decode("utf-8", "replace").strip() if branch else None,
                "commits": commits, "status": self.source_status()}

    def provenance_text(self, details, count):
        lines = ["Project: register", "Snapshot contents: current source files", "Source file count: %d" % count]
        if details["git"]:
            visible_status = [entry for entry in details["status"]
                              if entry[1] not in self.content_exclusions
                              and entry[2] not in self.content_exclusions]
            lines.extend([
                "Git branch: " + (details["branch"] or "(detached HEAD)"),
                "Git HEAD: " + (details["head"] or "(no commits yet)"),
                "Included source status: " + ("modified" if visible_status else "clean"),
                "", "Recent commits:",
            ])
            lines.extend("  %s %s" % entry for entry in details["commits"])
            if not details["commits"]:
                lines.append("  (none)")
            lines.extend(["", "Included source changes:"])
            for code, name, previous in visible_status:
                suffix = " (from %s)" % quoted(previous) if previous is not None else ""
                lines.append("  %s %s%s" % (code, quoted(name), suffix))
            if not visible_status:
                lines.append("  (none)")
        else:
            lines.append("Git: no repository rooted in the project folder; exported by directory traversal")
        lines.extend(["", "Git history, generated Register archives, runtime caches, virtual environments,",
                      "live Register data, SQLite files, and common secret-file names are excluded.",
                      "Source file modes are preserved. Internal relative symlinks are stored as links."])
        return "\n".join(lines) + "\n"

    def inside_project(self, path):
        try:
            path.resolve().relative_to(self.project)
            return True
        except (ValueError, RuntimeError):
            return False

    def read_entry(self, name):
        path = self.project / name
        if not self.inside_project(path.parent):
            raise ExportError("source parent points outside the project: %s" % quoted(name))
        try:
            before = path.lstat()
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(before.st_mode):
            target = os.readlink(path)
            if os.path.isabs(target) or not self.inside_project(path.parent / target):
                raise ExportError("external or absolute symlink cannot be exported: %s" % quoted(name))
            content = os.fsencode(target)
        elif stat.S_ISREG(before.st_mode):
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb") as source:
                if fingerprint(os.fstat(source.fileno())) != fingerprint(before):
                    raise ExportError("source changed during export; retry: %s" % quoted(name))
                content = source.read(16)
                if content == b"SQLite format 3\0":
                    self.content_exclusions.add(name)
                else:
                    content += source.read()
                if fingerprint(os.fstat(source.fileno())) != fingerprint(before):
                    raise ExportError("source changed during export; retry: %s" % quoted(name))
        else:
            raise ExportError("unsupported source entry (directory or special file): %s" % quoted(name))
        if fingerprint(path.lstat()) != fingerprint(before):
            raise ExportError("source changed during export; retry: %s" % quoted(name))
        if name in self.content_exclusions:
            return None, None, fingerprint(before)
        timestamp = datetime.datetime.fromtimestamp(before.st_mtime, datetime.timezone.utc)
        date = tuple(timestamp.timetuple()[:6]) if 1980 <= timestamp.year <= 2107 else (1980, 1, 1, 0, 0, 0)
        entry = zipfile.ZipInfo(ARCHIVE_PREFIX + name, date_time=date)
        entry.create_system = 3
        entry.external_attr = before.st_mode << 16
        entry.compress_type = zipfile.ZIP_DEFLATED
        return entry, content, fingerprint(before)

    def write(self):
        if os.path.lexists(self.project / PROVENANCE):
            raise ExportError("%s is reserved for snapshot provenance; rename the existing source file first" % PROVENANCE)
        if self.output.is_symlink():
            raise ExportError("output must not be a symlink: %s" % self.output)
        self.output.parent.mkdir(parents=True, exist_ok=True)
        names = self.source_names()
        before = self.provenance()
        descriptor, temporary = tempfile.mkstemp(prefix=".register-export-", suffix=".tmp", dir=self.output.parent)
        os.close(descriptor)
        staging = Path(temporary)
        captured = {}
        included_count = 0
        try:
            with zipfile.ZipFile(staging, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name in names:
                    result = self.read_entry(name)
                    if result is None:
                        captured[name] = None
                        continue
                    entry, content, identity = result
                    captured[name] = identity
                    if entry is None:
                        continue
                    archive.writestr(entry, content)
                    included_count += 1
                info = zipfile.ZipInfo(ARCHIVE_PREFIX + PROVENANCE)
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, self.provenance_text(before, included_count))
            if names != self.source_names() or before != self.provenance():
                raise ExportError("project changed during export; retry when edits finish")
            for name, identity in captured.items():
                try:
                    current = fingerprint((self.project / name).lstat())
                except FileNotFoundError:
                    current = None
                if current != identity:
                    raise ExportError("source changed during export; retry: %s" % quoted(name))
            with staging.open("rb") as complete:
                os.fsync(complete.fileno())
            os.replace(staging, self.output)
        finally:
            staging.unlink(missing_ok=True)
        return self.output


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export Register source without changing the project or Git history.")
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parent,
                        help="project folder (default: folder containing export.py)")
    parser.add_argument("--output", "-o", type=Path,
                        help="ZIP output (default: PROJECT/register-snapshot.zip; relative paths use PROJECT)")
    args = parser.parse_args(argv)
    project = args.project.expanduser().resolve()
    if not project.is_dir():
        parser.error("project folder does not exist: %s" % project)
    output = args.output.expanduser() if args.output is not None else Path(DEFAULT_OUTPUT)
    if not output.is_absolute():
        output = project / output
    output = Path(os.path.abspath(output))
    if output.suffix.lower() != ".zip":
        parser.error("output filename must end in .zip")
    try:
        destination = Exporter(project, output).write()
    except (ExportError, OSError, ValueError, UnicodeError, zipfile.BadZipFile) as error:
        print("register export: %s" % error, file=sys.stderr)
        return 1
    print(destination)
    return 0


if __name__ == "__main__":
    sys.exit(main())
