#!/usr/bin/env bash
set -euo pipefail

usage() {
    printf '%s\n' 'Usage: ./install.sh [--dependencies]' \
        'Install register and Bash completion for this user.' \
        '--dependencies also installs Python 3, file, xclip, and wl-clipboard using apt.'
}

install_dependencies=0
case "${1-}" in
    '') ;;
    --dependencies) install_dependencies=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
esac
(( $# <= 1 )) || { usage >&2; exit 2; }

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
for required in register register_support.py completion/register.bash; do
    [[ -f "$project_dir/$required" ]] || {
        printf 'Missing project file: %s\n' "$project_dir/$required" >&2
        exit 1
    }
done

if (( install_dependencies )); then
    missing_packages=()
    command -v python3 >/dev/null || missing_packages+=(python3)
    command -v file >/dev/null || missing_packages+=(file)
    command -v xclip >/dev/null || missing_packages+=(xclip)
    if ! command -v wl-copy >/dev/null || ! command -v wl-paste >/dev/null; then
        missing_packages+=(wl-clipboard)
    fi
    if (( ${#missing_packages[@]} == 0 )); then
        printf '%s\n' 'Dependencies are already available.'
    else
        command -v apt-get >/dev/null || { printf '%s\n' 'Dependency installation requires apt-get (Ubuntu/Debian).' >&2; exit 1; }
        if (( EUID == 0 )); then
            apt-get update
            apt-get install -y "${missing_packages[@]}"
        else
            command -v sudo >/dev/null || { printf '%s\n' 'Install the dependencies as an administrator, then rerun without --dependencies.' >&2; exit 1; }
            sudo apt-get update
            sudo apt-get install -y "${missing_packages[@]}"
        fi
    fi
fi

command -v python3 >/dev/null || {
    printf '%s\n' 'Python 3 is required. Run ./install.sh --dependencies on Ubuntu/Debian.' >&2
    exit 1
}

bin_dir="$HOME/.local/bin"
completion_dir="$HOME/.local/share/bash-completion/completions"
launcher="$bin_dir/register"
completion_file="$completion_dir/register"
bashrc="$HOME/.bashrc"
launcher_marker='# register launcher: managed by install.sh'
completion_marker='# register completion: managed by install.sh'

for destination in "$launcher" "$completion_file"; do
    if [[ -e "$destination" || -L "$destination" ]]; then
        marker=$launcher_marker
        [[ $destination != "$completion_file" ]] || marker=$completion_marker
        if [[ ! -f "$destination" ]] || ! grep -Fqx -- "$marker" "$destination"; then
            printf 'Refusing to replace an unrelated file: %s\n' "$destination" >&2
            exit 1
        fi
    fi
done

mkdir -p -- "$bin_dir" "$completion_dir"
launcher_tmp=$(mktemp "$bin_dir/.register.XXXXXX")
completion_tmp=$(mktemp "$completion_dir/.register.XXXXXX")
trap 'rm -f -- "$launcher_tmp" "$completion_tmp"' EXIT
{
    printf '#!/usr/bin/env bash\n%s\n' "$launcher_marker"
    printf 'exec python3 %q "$@"\n' "$project_dir/register"
} > "$launcher_tmp"
chmod 755 "$launcher_tmp"
cp -- "$project_dir/completion/register.bash" "$completion_tmp"
chmod 644 "$completion_tmp"
mv -f -- "$launcher_tmp" "$launcher"
mv -f -- "$completion_tmp" "$completion_file"

# A marked block makes completion work even without the bash-completion package.
if ! grep -Fqx '# >>> register installer >>>' "$bashrc" 2>/dev/null; then
    cat >> "$bashrc" <<'BASHRC'

# >>> register installer >>>
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) export PATH="$HOME/.local/bin:$PATH" ;;
esac
if [[ -r "$HOME/.local/share/bash-completion/completions/register" ]]; then
    source "$HOME/.local/share/bash-completion/completions/register"
fi
# <<< register installer <<<
BASHRC
    printf 'Added a marked PATH/completion block to %s\n' "$bashrc"
fi

printf 'Installed %s\n' "$launcher"
printf 'Enabled Bash completion in %s\n' "$completion_file"
printf '%s\n' 'Open a new Bash terminal, or activate in this shell:'
printf 'export PATH="$HOME/.local/bin:$PATH"; source %q\n' "$completion_file"
missing=()
for dependency in file xclip wl-copy wl-paste; do
    command -v "$dependency" >/dev/null || missing+=("$dependency")
done
if (( ${#missing[@]} )); then
    printf 'Optional helpers missing: %s\n' "${missing[*]}"
    printf '%s\n' 'For MIME detection and both desktop clipboard backends: ./install.sh --dependencies'
fi
