# bash completion for k7ctl
_k7ctl() {
    local cur prev words cword
    _init_completion 2>/dev/null || {
        cur=${COMP_WORDS[COMP_CWORD]}; prev=${COMP_WORDS[COMP_CWORD-1]}
        words=("${COMP_WORDS[@]}"); cword=$COMP_CWORD
    }
    local cmds="status rates latency keepawake eq volume-max setup firmware gui tray"
    local cmd=${words[1]}

    if [ "$cword" -eq 1 ]; then
        COMPREPLY=($(compgen -W "$cmds --help --version" -- "$cur"))
        return
    fi
    case $cmd in
        status)
            case $prev in
                --format) COMPREPLY=($(compgen -W "text json waybar short" -- "$cur")); return ;;
                --interval) return ;;
            esac
            COMPREPLY=($(compgen -W "--format --json --watch -w --interval --help" -- "$cur")) ;;
        rates)     [ "$cword" -eq 2 ] && COMPREPLY=($(compgen -W "enable disable status" -- "$cur")) ;;
        latency)   [ "$cword" -eq 2 ] && COMPREPLY=($(compgen -W "default stable low-latency" -- "$cur")) ;;
        keepawake) COMPREPLY=($(compgen -W "on off status --no-restart" -- "$cur")) ;;
        setup)     COMPREPLY=($(compgen -W "--yes -y --help" -- "$cur")) ;;
        eq)
            if [ "$cword" -eq 2 ]; then
                COMPREPLY=($(compgen -W "status list search get import remove on off" -- "$cur"))
                return
            fi
            case ${words[2]} in
                on|remove)
                    local IFS=$'\n'
                    local dir="${XDG_DATA_HOME:-$HOME/.local/share}/k7ctl/eq"
                    local profiles=$(ls "$dir" 2>/dev/null | sed 's/\.txt$//')
                    COMPREPLY=($(compgen -W "$profiles" -- "$cur"))
                    COMPREPLY=("${COMPREPLY[@]// /\\ }") ;;
                import)
                    [ "$prev" = --name ] && return
                    COMPREPLY=($(compgen -f -- "$cur") $(compgen -W "--name" -- "$cur")) ;;
                get)    COMPREPLY=($(compgen -W "--source --name --on" -- "$cur")) ;;
                search) COMPREPLY=($(compgen -W "--limit --refresh" -- "$cur")) ;;
            esac ;;
    esac
}
complete -F _k7ctl k7ctl
