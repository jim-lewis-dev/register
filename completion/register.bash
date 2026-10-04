# Bash completion for register. Source this file to enable it immediately.
# register completion: managed by install.sh
_register_completion() {
    local cur prev command word value name_count=0 skip_value=0 i
    COMPREPLY=()
    cur=${COMP_WORDS[COMP_CWORD]}
    prev=${COMP_WORDS[COMP_CWORD-1]}
    command=${COMP_WORDS[1]-}

    if (( COMP_CWORD == 1 )); then
        mapfile -t COMPREPLY < <(compgen -W 'add update copy get show remove run' -- "$cur")
        return
    fi

    # Everything after the run name belongs to the stored program.
    if [[ $command == run ]]; then
        if (( COMP_CWORD == 2 )); then
            if [[ $cur == -* ]]; then
                mapfile -t COMPREPLY < <(compgen -W '--help' -- "$cur")
            else
                mapfile -t COMPREPLY < <("${COMP_WORDS[0]}" _complete --exec "$cur" 2>/dev/null)
            fi
        else
            compopt -o default 2>/dev/null || :
        fi
        return
    fi

    case "$prev" in
        --source)
            compopt -o filenames 2>/dev/null || :
            mapfile -t COMPREPLY < <(compgen -f -- "$cur"; compgen -W 'CLIPBOARD PRIMARY -' -- "$cur")
            return ;;
        --to)
            mapfile -t COMPREPLY < <(compgen -W 'CLIPBOARD PRIMARY' -- "$cur")
            return ;;
        --mime)
            mapfile -t COMPREPLY < <(compgen -W 'text/plain application/octet-stream image/png image/jpeg image/gif image/webp application/pdf' -- "$cur")
            return ;;
    esac

    case "$cur" in
        --source=*)
            value=${cur#--source=}
            compopt -o filenames 2>/dev/null || :
            while IFS= read -r word; do COMPREPLY+=("--source=$word"); done < <(
                compgen -f -- "$value"
                compgen -W 'CLIPBOARD PRIMARY -' -- "$value"
            )
            return ;;
        --to=*)
            value=${cur#--to=}
            while IFS= read -r word; do COMPREPLY+=("--to=$word"); done < <(
                compgen -W 'CLIPBOARD PRIMARY' -- "$value"
            )
            return ;;
        --mime=*)
            value=${cur#--mime=}
            while IFS= read -r word; do COMPREPLY+=("--mime=$word"); done < <(
                compgen -W 'text/plain application/octet-stream image/png image/jpeg image/gif image/webp application/pdf' -- "$value"
            )
            return ;;
    esac

    if [[ $cur == -* ]]; then
        case "$command" in
            add) word='--source --exec --no-exec --utf8 --binary --mime --help' ;;
            update) word='--source --keep-content --exec --no-exec --utf8 --binary --mime --help' ;;
            copy) word='--exec --no-exec --utf8 --binary --mime --help' ;;
            get) word='--to --help' ;;
            show|remove) word='--help' ;;
            *) return ;;
        esac
        mapfile -t COMPREPLY < <(compgen -W "$word" -- "$cur")
        return
    fi

    # Count names before the cursor, excluding option arguments.
    for ((i=2; i<COMP_CWORD; i++)); do
        word=${COMP_WORDS[i]}
        if (( skip_value )); then skip_value=0; continue; fi
        case "$word" in
            --source|--to|--mime) skip_value=1 ;;
            -*) ;;
            *) ((name_count+=1)) ;;
        esac
    done
    case "$command" in
        get|update|remove) (( name_count == 0 )) || return ;;
        copy) (( name_count < 2 )) || return ;;
        show) ;;
        *) return ;;
    esac
    mapfile -t COMPREPLY < <("${COMP_WORDS[0]}" _complete "$cur" 2>/dev/null)
}
complete -F _register_completion register
