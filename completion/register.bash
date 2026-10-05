# Bash completion for register. Source this file to enable it immediately.
# register completion: managed by install.sh
_register_completion() {
    local cur command word value flags mime_values i
    local position=0 need_mime=0 end_options=0
    COMPREPLY=()
    cur=${COMP_WORDS[COMP_CWORD]}
    command=${COMP_WORDS[1]-}
    mime_values='text/plain application/octet-stream image/png image/jpeg image/gif image/webp application/pdf'

    if (( COMP_CWORD == 1 )); then
        mapfile -t COMPREPLY < <(compgen -W 'add update copy get show remove run' -- "$cur")
        return
    fi

    # Count positional arguments, excluding flags and their MIME values.
    for ((i=2; i<COMP_CWORD; i++)); do
        word=${COMP_WORDS[i]}
        if (( need_mime )); then
            [[ $word == = ]] || need_mime=0
            continue
        fi
        if (( ! end_options )); then
            case "$word" in
                --) end_options=1; continue ;;
                --mime)
                    [[ $command =~ ^(add|update|copy)$ ]] && need_mime=1
                    continue ;;
                -) ;;
                -*) continue ;;
            esac
        fi
        ((position+=1))
        # Everything after the run name belongs to the stored program.
        if [[ $command == run ]]; then
            compopt -o default 2>/dev/null || :
            return
        fi
    done

    if (( need_mime )); then
        [[ $cur != = ]] || cur=''
        mapfile -t COMPREPLY < <(compgen -W "$mime_values" -- "$cur")
        return
    fi
    if (( ! end_options )) && [[ $command =~ ^(add|update|copy)$ && $cur == --mime=* ]]; then
        value=${cur#--mime=}
        while IFS= read -r word; do COMPREPLY+=("--mime=$word"); done < <(
            compgen -W "$mime_values" -- "$value"
        )
        return
    fi

    if (( ! end_options )) && [[ $cur == -* ]]; then
        case "$command" in
            add|update|copy) flags='--exec --no-exec --utf8 --binary --mime --help --' ;;
            get|show|remove|run) flags='--help --' ;;
            *) return ;;
        esac
        mapfile -t COMPREPLY < <(compgen -W "$flags" -- "$cur")
        return
    fi

    case "$command" in
        add|update)
            if (( position == 1 )); then
                compopt -o filenames 2>/dev/null || :
                while IFS= read -r word; do
                    if [[ $word == CLIPBOARD ]] || { (( ! end_options )) && [[ $word == -* ]]; }; then
                        word="./$word"
                    fi
                    COMPREPLY+=("$word")
                done < <(compgen -f -- "$cur")
                [[ CLIPBOARD != "$cur"* ]] || COMPREPLY+=('CLIPBOARD')
                return
            fi
            [[ $command == update ]] && (( position == 0 )) || return ;;
        get)
            if (( position == 1 )); then
                mapfile -t COMPREPLY < <(compgen -W 'CLIPBOARD' -- "$cur")
                return
            fi
            (( position == 0 )) || return ;;
        copy) (( position < 2 )) || return ;;
        remove|run) (( position == 0 )) || return ;;
        show) ;;
        *) return ;;
    esac
    if [[ $command == run ]]; then
        mapfile -t COMPREPLY < <("${COMP_WORDS[0]}" _complete --exec "$cur" 2>/dev/null)
    else
        mapfile -t COMPREPLY < <("${COMP_WORDS[0]}" _complete "$cur" 2>/dev/null)
    fi
}
complete -F _register_completion register
