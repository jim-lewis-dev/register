#!/usr/bin/env bash
set -euo pipefail

if (( $# )); then
    printf '%s\n' 'Usage: ./uninstall.sh' 'Remove the user launcher and Bash completion. Keep the project and all registers.'
    [[ $# == 1 && ( $1 == --help || $1 == -h ) ]] && exit 0
    exit 2
fi

launcher="$HOME/.local/bin/register"
completion_file="$HOME/.local/share/bash-completion/completions/register"
for target in "$launcher" "$completion_file"; do
    marker='# register launcher: managed by install.sh'
    [[ $target != "$completion_file" ]] || marker='# register completion: managed by install.sh'
    if [[ -f "$target" ]] && grep -Fqx -- "$marker" "$target"; then
        rm -- "$target"
        printf 'Removed %s\n' "$target"
    elif [[ -e "$target" || -L "$target" ]]; then
        printf 'Left unrelated file untouched: %s\n' "$target"
    fi
done

if [[ -f "$HOME/.bashrc" ]]; then
    python3 - "$HOME/.bashrc" <<'PY'
import os
from pathlib import Path
import stat
import sys
import tempfile

path = Path(sys.argv[1])
if path.is_symlink():
    path = path.resolve(strict=True)
text = path.read_bytes()
start_marker = b"# >>> register installer >>>\n"
end_marker = b"# <<< register installer <<<\n"
start = text.find(start_marker)
if start != -1:
    end = text.find(end_marker, start + len(start_marker))
    if end != -1:
        block = text[start:end + len(end_marker)]
        # Older installs combined PATH and completion. Retain their shared PATH
        # setup so removing Register does not hide other user-installed tools.
        shared = b'''# >>> user local bin >>>
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) export PATH="$HOME/.local/bin:$PATH" ;;
esac
# <<< user local bin <<<
'''
        preserve_path = b'case ":$PATH:" in' in block and b"$HOME/.local/bin" in block
        replacement = shared if preserve_path and b"# >>> user local bin >>>\n" not in text else b""
        text = text[:start] + replacement + text[end + len(end_marker):]
        descriptor, temporary = tempfile.mkstemp(prefix=".bashrc-register-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), stat.S_IMODE(path.stat().st_mode))
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        print(f"Removed the marked register block from {path}")
PY
fi
printf '%s\n' 'Uninstalled. The project, saved registers, and shared user PATH are unchanged.' \
    'In the current Bash shell, run: complete -r register 2>/dev/null; unset -f _register_completion; hash -r'
