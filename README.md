# register

Persistent named buffers for Linux: capture text, file contents, images, or commands; copy them, pipe them elsewhere, paste them, or run them. A register stores bytes plus `exec`, `utf8`, and MIME metadata.

## Install

Keep this project at `~/projects/register`. On Ubuntu/Debian:

```bash
cd ~/projects/register
./install.sh --dependencies
export PATH="$HOME/.local/bin:$PATH"
source completion/register.bash
```

The installer uses `apt` only with `--dependencies`, and skips it when all helpers are present. Run `./install.sh` to install only the tool. Python 3 runs the tool; `file` improves MIME detection, `xclip` handles X11, and `wl-clipboard` handles Wayland. Both clipboard backends are called internally.

Installation creates a user launcher and Bash completion, then adds one marked PATH/completion block to `~/.bashrc`. New Bash terminals get completion automatically. Keep the project directory in place, or rerun the installer after moving it. `./uninstall.sh` removes these installed hooks and keeps your project and registers.

## Use

```bash
printf 'hello\n' | register add greeting
register get greeting
register get greeting | tr a-z A-Z | register update greeting
register copy greeting spare
register get spare > saved.txt
register show
register remove spare
```

Clipboard and images:

```bash
register add clip --source CLIPBOARD
register get clip --to PRIMARY
register add picture --source screenshot.png
register get picture --to CLIPBOARD
register add captured --source CLIPBOARD
register get captured > captured.png
```

Image bytes and their MIME type are retained. Clipboard capture chooses a supported image representation when available, otherwise text. Pasting advertises the stored MIME type; the receiving application decides whether it accepts that type. Copying a file in a file manager may put a file reference on the clipboard rather than the file's contents; use `--source PATH` to import actual file bytes. PRIMARY availability depends on the desktop.

Store and execute commands:

```bash
printf 'printf "Hello, %%s\\n" "$1"\n' | register add greet --exec
register run greet world
register run greet world | register add result
register get result
```

`run` executes a marked executable. A shebang chooses its interpreter; a snippet without a shebang runs in Bash. Native programs require their normal system dependencies. Arguments, stdin, stdout, and stderr pass through, and the child's exit status is returned. A stored `cd` affects only the child process.

## Commands

| Command | Behavior |
| --- | --- |
| `add NAME [--source SOURCE]` | Create; error if the name exists. |
| `update NAME [--source SOURCE]` | Replace content of an existing register. |
| `update NAME --keep-content` | Change flags or MIME without consuming stdin. |
| `copy A B` | Copy bytes and metadata; overwrite B if it exists. |
| `get NAME [--to CLIPBOARD\|PRIMARY]` | Emit exact bytes to stdout, or send them to a desktop selection. |
| `show [NAME ...]` | JSON metadata; without names, list all in name order. |
| `remove NAME` | Delete the register. |
| `run NAME [ARGS ...]` | Run an executable register. |

`SOURCE` is a file path, `CLIPBOARD`, `PRIMARY`, or `-` for stdin. An explicit source takes precedence over piped input. Without `--source`, nonterminal stdin supplies content; empty input clears it. With terminal stdin, `add` creates an empty buffer and `update` preserves content. Use `--keep-content` for flags-only updates in automation.

`add`, `update`, and `copy` accept `--exec`/`--no-exec`, `--utf8`/`--binary`, and `--mime TYPE`. Imported bytes determine text/binary and MIME defaults; explicit flags override them. MIME inference is a heuristic and never changes the bytes. New content is detected again. `update` preserves the executable flag unless changed; `copy` inherits A's flags unless overridden. `add` defaults to nonexecutable. Content is never rewritten to match metadata.

Names use letters, digits, dots, underscores, and hyphens; no leading hyphen. `.`, `..`, `CLIPBOARD`, and `PRIMARY` are reserved. Names identify copied bytes, not live links to source files. `show` reports `name`, `exec`, `utf8`, `mime`, and byte `size`.

Tab completes commands, options, files, and existing names. `run` offers only executable names; a new name for `add` or a `copy` destination is still yours to type.

## Storage

Registers live in the private SQLite database `~/.register/register.sqlite3`. Content and metadata change together in a transaction. Existing metadata-and-file registers are imported automatically on first use. Their old files remain until the database import is durably saved, then the obsolete copies are removed; your buffers retain their bytes and flags. Keep this personal data separate from the project's Git repository.

Run the project's tests with `python3 -m unittest discover -s tests`.
