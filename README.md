# register

Persistent named buffers for Linux. Save text, file contents, images, or commands; retrieve exact bytes, send them to the clipboard, or execute a saved command.

Buffers survive new terminals and reboots. Keep a useful input, the command that transforms it, and the result under separate names. JSON arrays, NUL-separated filenames, and PDFs are all ordinary byte buffers; their consuming programs decide how to interpret them.

```bash
register add greeting 'hello world'
register get greeting | tr a-z A-Z | register update greeting
register get greeting CLIPBOARD
register add greet 'printf "Hello, %s\n" "$1"' --exec
register run greet world
register show
```

## Optional shell setup

The program runs directly with `python3 register`. To make `register` available everywhere with Bash Tab completion:

```bash
cd ~/projects/register
./install.sh
```

Open a new Bash terminal afterward. Installation creates a launcher in `~/.local/bin`, a completion loader, and marked PATH and completion blocks in `~/.bashrc`. Both launcher and completion follow the project files; ordinary project updates need no reinstall. Rerun installation after moving the project.

If an earlier installation copied the completion file, run `./install.sh` once and open a new Bash terminal to switch to the project-following loader. Future updates need no reinstall.

Python 3.10 or newer and Bash are required; there are no Python package dependencies. On Ubuntu/Debian, `./install.sh --dependencies` also uses apt to install missing runtime and clipboard helpers. The program chooses the desktop clipboard backend automatically. `./uninstall.sh` removes the register launcher and completion hooks, keeping the shared PATH setup, project, saved registers, and installed packages. Install and uninstall are optional shell conveniences; project updates do not run either one.

## Commands

| Command | Behavior |
| --- | --- |
| `register add NAME [OBJECT]` | Create; error if NAME already exists. |
| `register update NAME [OBJECT]` | Update content or properties; error if NAME is missing. |
| `register copy A B` | Copy bytes and all properties; overwrite B if it exists. |
| `register get NAME` | Write exact bytes to stdout, without adding a newline. |
| `register get NAME CLIPBOARD` | Put the stored content on the clipboard. |
| `register show [NAME ...]` | JSON properties; no names lists all registers in name order. |
| `register remove NAME` | Delete a register. |
| `register run NAME [ARGS ...]` | Execute a register marked executable. |

Use `register --help` or `register COMMAND --help` for command help. Redirect retrieval to save a file: `register get NAME > FILE`.

## Input and properties

`add` and `update` interpret OBJECT in this order:

1. `CLIPBOARD` captures the clipboard.
2. An existing regular file supplies its bytes. This is a snapshot, independent of later changes to that file. Directories, broken symlinks, special files, and unreadable files give errors.
3. Otherwise, OBJECT is literal text. A nonexistent path is stored as text without a warning.

Quotes only preserve spaces. `'README.md'` imports that file if it exists. To store the filename itself, pipe it: `printf '%s' 'README.md' | register add filename`. Piped content is always literal bytes.

An explicit OBJECT takes precedence over stdin. An explicit empty string `''` means empty content. For an object beginning with a hyphen, end option parsing first: `register add option-text -- '-value'`. A single `-` follows the ordinary file-or-text rule.

Without OBJECT, pipes and redirected stdin supply exact bytes. Empty input clears an updated register, including `register update NAME < /dev/null`. With terminal stdin, `add NAME` creates an empty buffer and `update NAME` keeps its contents.

`add`, `update`, and `copy` accept these optional property overrides:

| Property | Flags | Default |
| --- | --- | --- |
| Executable | `--exec`, `--no-exec` | `add` inherits file execute permission; other input starts nonexecutable. `update` preserves the existing value; `copy` inherits A. |
| Text/binary | `--utf8`, `--binary` | Detected from new bytes; otherwise preserved. |
| MIME type | `--mime TYPE` | Detected from new bytes or captured clipboard type; otherwise preserved. |

Only specified properties are overridden. Detection never rewrites content or treats ordinary text as a command. In a terminal, `register update NAME --exec` preserves content. With a pipe, it replaces content and sets the property. To change flags while preserving bytes in any context, use `register copy NAME NAME --exec`.

Names use letters, digits, dots, underscores, and hyphens, with no leading hyphen. `.`, `..`, and `CLIPBOARD` are reserved. Names identify registers independently of source filenames. `show` reports `name`, `exec`, `utf8`, `mime`, and byte `size`.

Completion is installed once and queries current register names on every Tab. `run` offers only executable names. Commands, options, and input files also complete; new register names are yours to type.

## Clipboard and execution

```bash
register add clip CLIPBOARD
register get clip CLIPBOARD
register add picture screenshot.png
register get picture CLIPBOARD
```

Clipboard capture prefers an offered image representation, otherwise text. Image bytes and their MIME type are retained, including PNG and JPEG. Pasting depends on whether the receiving application accepts that type. A file-manager copy may offer a file reference; pass the file path to store actual contents.

Saving never executes content. `run` requires the executable property. A command without a shebang runs in Bash; a shebang chooses its interpreter. Native programs need their normal dependencies. Arguments, stdin, stdout, stderr, current directory, and exit status pass through. A saved `cd` affects only the child process.

See [EXAMPLES.md](EXAMPLES.md) for a JSON-array pick list, Bash filename arrays, reusable replies, binary snapshots, and clipboard workflows. Each example runs in an isolated store.

## Project updates and snapshots

Keep `update.py` and `export.py` in this project folder. Commit or remove local changes before updating. Download `register_update.zip` to `~/Downloads`, then run from the project folder:

```bash
python3 update.py
```

The updater selects the newest matching download, including browser duplicate names such as `register_update (1).zip`. It requires a clean Git repository, including untracked files. It validates the package and baseline, applies changes, runs tests, commits, then deletes the selected ZIP after success. It runs no installer and makes no shell or package changes. Failures before commit keep the ZIP and restore touched files and the Git index if no other process has committed in the meantime. An explicit path also works: `python3 update.py /path/to/register_update.zip`; archives outside Downloads are kept. Open a new Bash terminal after an update to load any completion changes.

To send your current project for review:

```bash
python3 export.py
```

This creates `register-snapshot.zip` beside the script, including current source, uncommitted work, and Git context. It works without Git and makes no commit. Generated archives, Git history, caches, private credentials, and register databases are excluded.

## Storage and tests

Saved registers live in `~/.register/register.sqlite3`, separately from project source. SQLite changes content and properties together. `REGISTER_HOME` selects another store. Existing metadata-and-file stores are imported automatically; their obsolete copies are removed only after successful import.

Run the complete suite from the project folder:

```bash
python3 -m unittest discover -s tests -v
```

The tests invoke the real CLI in temporary stores and check returned bytes, properties, exit codes, and side effects. They cover file snapshots, pipes and empty input, option placement, command arguments, Bash/native execution, failed writes, concurrent first use, and live completion queries. Separate tests exercise install/uninstall in temporary home folders, export exclusions, and update success or rollback in disposable Git repositories. These checks do not use your saved registers or install packages.

Clipboard tests simulate both desktop backends to verify bytes and MIME handling. They cannot prove that a particular desktop application accepts a paste; the clipboard examples are the manual check. CI runs the suite with the Ubuntu 22.04 and 24.04 system Pythons and a current Python, so parser differences across interpreter releases are exercised.
