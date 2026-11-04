# fish completion for k7ctl
set -l cmds status rates latency keepawake eq volume-max setup firmware tray
set -l eqacts status list search get import remove on off

function __k7ctl_profiles
    set -l dir (set -q XDG_DATA_HOME; and echo $XDG_DATA_HOME; or echo $HOME/.local/share)/k7ctl/eq
    for f in $dir/*.txt
        basename $f .txt
    end
end

complete -c k7ctl -f
complete -c k7ctl -n "not __fish_seen_subcommand_from $cmds" -l version -d 'Show version'
complete -c k7ctl -n "not __fish_seen_subcommand_from $cmds" -a status -d 'Show what the K7 is playing'
complete -c k7ctl -n "not __fish_seen_subcommand_from $cmds" -a rates -d "Use the music's own sample rate"
complete -c k7ctl -n "not __fish_seen_subcommand_from $cmds" -a latency -d 'Buffer size presets'
complete -c k7ctl -n "not __fish_seen_subcommand_from $cmds" -a keepawake -d 'Stop the K7 suspending when idle'
complete -c k7ctl -n "not __fish_seen_subcommand_from $cmds" -a eq -d 'Headphone EQ (AutoEQ profiles)'
complete -c k7ctl -n "not __fish_seen_subcommand_from $cmds" -a volume-max -d "Set PipeWire's K7 volume to 100%"
complete -c k7ctl -n "not __fish_seen_subcommand_from $cmds" -a setup -d 'Bit-perfect setup in one go'
complete -c k7ctl -n "not __fish_seen_subcommand_from $cmds" -a firmware -d 'Firmware version and how to update'
complete -c k7ctl -n "not __fish_seen_subcommand_from $cmds" -a tray -d 'System tray icon'

complete -c k7ctl -n "__fish_seen_subcommand_from status" -l format -x -a 'text json waybar short' -d 'Output format'
complete -c k7ctl -n "__fish_seen_subcommand_from status" -l json -d 'Same as --format json'
complete -c k7ctl -n "__fish_seen_subcommand_from status" -s w -l watch -d 'Keep running, print changes'
complete -c k7ctl -n "__fish_seen_subcommand_from status" -l interval -x -d 'Seconds between checks'
complete -c k7ctl -n "__fish_seen_subcommand_from rates" -a 'enable disable status'
complete -c k7ctl -n "__fish_seen_subcommand_from latency" -a 'default stable low-latency'
complete -c k7ctl -n "__fish_seen_subcommand_from keepawake" -a 'on off status'
complete -c k7ctl -n "__fish_seen_subcommand_from keepawake" -l no-restart -d "Don't restart WirePlumber now"
complete -c k7ctl -n "__fish_seen_subcommand_from setup" -s y -l yes -d "Don't ask before setting volume to 100%"

complete -c k7ctl -n "__fish_seen_subcommand_from eq; and not __fish_seen_subcommand_from $eqacts" -a "$eqacts"
complete -c k7ctl -n "__fish_seen_subcommand_from eq; and __fish_seen_subcommand_from on remove" -a '(__k7ctl_profiles)'
complete -c k7ctl -n "__fish_seen_subcommand_from eq; and __fish_seen_subcommand_from import" -F
complete -c k7ctl -n "__fish_seen_subcommand_from eq; and __fish_seen_subcommand_from import get" -l name -x -d 'Save under this name'
complete -c k7ctl -n "__fish_seen_subcommand_from eq; and __fish_seen_subcommand_from get" -l source -x -d 'Measurement source'
complete -c k7ctl -n "__fish_seen_subcommand_from eq; and __fish_seen_subcommand_from get" -l on -d 'Turn EQ on with it'
complete -c k7ctl -n "__fish_seen_subcommand_from eq; and __fish_seen_subcommand_from search" -l limit -x -d 'Max results'
complete -c k7ctl -n "__fish_seen_subcommand_from eq; and __fish_seen_subcommand_from search" -l refresh -d 'Re-download the index'
