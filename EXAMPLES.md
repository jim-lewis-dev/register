# Useful register workflows

Run these in Bash. Start in the project folder; this setup uses the project command directly and a temporary store, so no installation is needed:

```bash
export PATH="$PWD:$PATH"
export REGISTER_HOME="$(mktemp -d)"
```

Run the examples in order, once per temporary store. `add` deliberately refuses to overwrite an existing name. At the end, `unset REGISTER_HOME` returns to your normal registers. The temporary folder remains available for inspecting the generated files. Clipboard steps are optional and deliberately replace the desktop clipboard.

## Keep a JSON array, a useful filter, and its result

Save a small order queue:

```bash
register add demo-orders <<'JSON'
[
  {"item": "USB-C cable", "quantity": 3, "bin": "A2", "status": "ready"},
  {"item": "Monitor arm", "quantity": 1, "bin": "B4", "status": "waiting"},
  {"item": "Ethernet adapter", "quantity": 2, "bin": "A7", "status": "ready"}
]
JSON
```

Save the transformation once. It reads JSON from stdin and accepts the desired status as an argument:

```bash
register add demo-picklist --exec <<'BASH'
python3 -c '
import json, sys
wanted = sys.argv[1] if len(sys.argv) > 1 else "ready"
for row in json.load(sys.stdin):
    if row["status"] == wanted:
        print("{}\t{}\t{}".format(row["bin"], row["quantity"], row["item"]))
' "$@"
BASH
register get demo-orders | register run demo-picklist ready | register add demo-today
register get demo-today
```

The result is two tab-separated rows, suitable for pasting into a spreadsheet:

```text
A2      3       USB-C cable
A7      2       Ethernet adapter
```

The same saved program answers a different question without changing the input:

```bash
register get demo-orders | register run demo-picklist waiting
```

That prints the monitor-arm row. To replace the queue later, pipe fresh JSON into `register update demo-orders`. Register preserves the array as bytes; Python interprets it. No array-specific register command is needed.

## Keep a file selection as a Bash array

Select once, then reuse exactly those filenames. NUL separators preserve spaces and even embedded newlines:

```bash
mkdir -p "$REGISTER_HOME/documents"
(
    cd "$REGISTER_HOME/documents"
    printf 'Invoice paid\n' > 'invoice one.txt'
    printf 'Invoice pending\n' > $'invoice\ntwo.txt'
    find . -maxdepth 1 -type f -name '*.txt' -print0 | register add demo-selected
    mapfile -d '' -t files < <(register get demo-selected)
    printf 'Selected: %q\n' "${files[@]}"
    tar -cf "$REGISTER_HOME/invoices.tar" -- "${files[@]}"
)
```

This creates an archive containing both selected files. The buffer holds filenames, not their contents; reuse relative names from the same directory. Keep the quoted `"${files[@]}"`: it passes each filename as one argument. No `eval` or splitting on spaces is needed. For real work, replace the sample-file creation and `find` expression with your own selection.

## Turn a reusable reply into a draft

Keep variable parts as arguments instead of editing the template each time:

```bash
register add demo-reply 'printf "Hello %s,\n\nI can meet on %s.\n" "$1" "$2"' --exec
register run demo-reply Morgan 'Thursday at 2 PM' | register add demo-draft
register get demo-draft
register run demo-reply Casey 'Friday at 10 AM' | register update demo-draft
register get demo-draft
```

`demo-reply` is executable Bash; `demo-draft` is ordinary text. `register get demo-reply` retrieves the template itself. To paste the latest draft into an application:

```bash
register get demo-draft CLIPBOARD
```

Copying a register also copies its properties. This makes a reusable command available for editing as plain text without losing the original:

```bash
register copy demo-reply demo-reply-text --no-exec
register show demo-reply demo-reply-text
register get demo-reply-text
```

`copy` overwrites its destination. Empty input deliberately clears an existing draft:

```bash
register update demo-draft < /dev/null
register show demo-draft
```

The draft's reported size is now zero.

## Preserve an image independently of its source file

Generate a small blue PNG using only Python's standard library:

```bash
python3 - <<'PY'
import os, struct, zlib
from pathlib import Path

def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

header = struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0)
pixels = (b"\0" + b"\x20\x80\xe0" * 64) * 64
png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")
Path(os.environ["REGISTER_HOME"], "swatch.png").write_bytes(png)
PY
register add demo-image "$REGISTER_HOME/swatch.png"
register get demo-image > "$REGISTER_HOME/restored.png"
cmp "$REGISTER_HOME/swatch.png" "$REGISTER_HOME/restored.png"
register show demo-image
```

No `cmp` output means the bytes match. Metadata identifies `image/png` automatically. The stored image survives deletion or modification of the original. PDFs, archives, and other binary files use the same `add`/`get` commands. To paste this image into an image-capable application:

```bash
register get demo-image CLIPBOARD
```

This sends image content, not a filename. The receiving application must accept image pastes.

## Keep and clean the clipboard

Copy some text in a desktop application, then run:

```bash
register add demo-original CLIPBOARD
register get demo-original |
    sed 's/\r$//; s/[[:blank:]]*$//' |
    register add demo-cleaned
register get demo-cleaned CLIPBOARD
```

Paste the cleaned text to check it. `register get demo-original CLIPBOARD` restores the original. This is a manual clipboard history with names you choose.

The same capture command saves an image: copy a screenshot in an image-capable application, run `register add demo-screenshot CLIPBOARD`, then `register get demo-screenshot CLIPBOARD` when you want it back. There is no desktop-specific syntax to remember.

## Find what you saved

`register show` lists all names and properties. With optional Bash completion installed, type `register get demo-` and press Tab; `register run demo-` offers only executable entries. New names appear immediately without reinstalling or editing completion.
