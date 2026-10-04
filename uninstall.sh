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
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
text = path.read_bytes()
start_marker = b"# >>> register installer >>>\n"
end_marker = b"# <<< register installer <<<\n"
start = text.find(start_marker)
if start != -1:
    end = text.find(end_marker, start + len(start_marker))
    if end != -1:
        text = text[:start] + text[end + len(end_marker):]
        path.write_bytes(text)
        print(f"Removed the marked register block from {path}")
PY
fi
printf '%s\n' 'Uninstalled. The project and saved registers are unchanged.' \
    'In the current Bash shell, run: complete -r register 2>/dev/null; hash -r'
