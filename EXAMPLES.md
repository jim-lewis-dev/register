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

## Desktop checks

These three checks cover the installed command, live completion, and real application pastes. From the project folder, run `./install.sh --dependencies`, then open a **new Bash terminal**. Keep that same terminal for all three checks. Use a blank LibreOffice Writer document, or another application that accepts both text and image pastes. The checks replace the desktop clipboard.

Start a separate temporary store:

```bash
export REGISTER_HOME="$(mktemp -d)"
printf 'Test store: %s\n' "$REGISTER_HOME"
register add check-text 'register text: café 42'
register add check-command 'printf "register command works\n"' --exec
```

### 1. Complete names as they change

Type `register get check-` and press Tab twice. It should offer `check-text` and `check-command`. Press Ctrl+U to clear the unfinished command, then run:

```bash
register add check-later 'added after the first completion'
```

Type `register get check-` and press Tab twice again: `check-later` must now appear too. Clear the line with Ctrl+U. Type `register run check-` and press Tab: it should complete only `check-command`. Press Enter; expect `register command works`.

### 2. Paste and recapture exact text

Copy and run this whole block. It publishes the text and verifies recapture before you need to copy another command, which would replace the clipboard.

```bash
(
    set -e
    register get check-text CLIPBOARD
    register add check-text-back CLIPBOARD
    cmp <(register get check-text) <(register get check-text-back)
    printf 'Text bytes match. Paste into the document now.\n'
)
```

Expect `Text bytes match`. Switch to the document and press Ctrl+V: expect exactly `register text: café 42`.

### 3. Save and restore an actual image clipboard

Copy and run this whole block **first**. At its first prompt, copy a screenshot to the clipboard, or right-click a browser image and choose **Copy image**. Return to the terminal and press Enter. At its second prompt, paste into the document to confirm you get the test text, then return and press Enter again.

```bash
(
    set -e
    read -r -p 'Copy an image now, then return and press Enter: '
    register add check-image CLIPBOARD
    register show check-image
    register get check-text CLIPBOARD
    read -r -p 'Paste into the document: expect text. Return and press Enter: '
    register get check-image CLIPBOARD
    register add check-image-back CLIPBOARD
    cmp <(register get check-image) <(register get check-image-back)
    printf 'Image bytes match. Paste into the document: expect the original image.\n'
)
```

The displayed metadata should have a `mime` beginning with `image/` and a `size` greater than zero. After `Image bytes match`, switch to the document and press Ctrl+V: the original image should appear. This checks that the image came back from its register after the clipboard had been replaced.

When finished, return to your normal registers:

```bash
unset REGISTER_HOME
```

The temporary store remains at the path printed earlier. Repeat the checks on each desktop you use. If a check fails, keep the command output and the receiving application's name.
