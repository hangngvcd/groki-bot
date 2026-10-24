#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail

fail() { printf '%s\n' "$*" >&2; exit 1; }
dry=0
service_home=$HOME
gateway_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
config=''
agents_dir=''
command_name=''
while (($#)); do
    case "$1" in
        --dry-run) dry=1; shift ;;
        --config|--home|--gateway-dir|--launch-agents-dir)
            (($# >= 2)) || fail "Missing flag value"
            case "$1" in
                --config) config=$2 ;;
                --home) service_home=$2 ;;
                --gateway-dir) gateway_dir=$2 ;;
                --launch-agents-dir) agents_dir=$2 ;;
            esac
            shift 2 ;;
        install|uninstall|restart|status|check)
            [[ -z "$command_name" ]] || fail 'Only one command allowed'
            command_name=$1; shift ;;
        *) fail 'Unknown argument' ;;
    esac
done
[[ -n "$command_name" ]] || fail 'Usage: xiaoya_services.sh [flags] install|uninstall|restart|status|check'
config=${config:-$service_home/.stackchan/xiaoya-services.env}
agents_dir=${agents_dir:-$service_home/Library/LaunchAgents}
wrapper=$service_home/.stackchan/bin/stackchan-parent
logs=$service_home/Library/Logs/xiaoya
service_path=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin
keys=(PATH HOME)
values=("$service_path" "$service_home")
put() {
    local i
    for ((i=0; i<${#keys[@]}; i++)); do
        if [[ ${keys[i]} == "$1" ]]; then values[i]=$2; return; fi
    done
    keys+=("$1"); values+=("$2")
}
get() {
    local i
    for ((i=${#keys[@]}-1; i>=0; i--)); do
        if [[ ${keys[i]} == "$1" ]]; then printf '%s' "${values[i]}"; return; fi
    done
}
validate() {
    [[ -f "$config" ]] || fail 'Config file missing; copy xiaoya-services.env.example outside the repo'
    local mode line key value n=0
    mode=$(stat -f '%Lp' "$config" 2>/dev/null) || mode=$(stat -c '%a' "$config")
    [[ "$mode" =~ ^[0-7]+$ ]] || fail 'Cannot read config permissions'
    (( (8#$mode & 044) == 0 )) || fail 'Config is group/world readable; run chmod 600 on the config file'
    while IFS= read -r line || [[ -n "$line" ]]; do
        n=$((n+1))
        [[ "$line" =~ ^[[:space:]]*$ || "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" =~ ^[A-Z_][A-Z0-9_]*= ]] || fail "Invalid config line $n (expected KEY=VALUE)"
        key=${line%%=*}; value=${line#*=}
        case "$value" in
            *YOUR_AGENT_ID*|*YOUR_AGENT_NAME*|*generate-a-long-random-token*) fail "Replace placeholders in config (line $n)" ;;
        esac
        case "$key" in
            STACKCHAN_GBOT_VOICE_BOT_ID|STACKCHAN_TOOL_BOT|STACKCHAN_TOOL_BOT_ID|STACKCHAN_TOKEN|STACKCHAN_WAKE_WORD|STACKCHAN_KWS_THRESHOLD|STACKCHAN_KWS_SCORE|STACKCHAN_GBOT_VOICE_LANGUAGE|STACKCHAN_GBOT_VOICE_NAME|STACKCHAN_WAKE_IDLE_S|STACKCHAN_KWS_MODEL_DIR|UV_BIN|NODE_BIN) put "$key" "$value" ;;
            *) ;; # Only the documented allowlist reaches the services.
        esac
    done < "$config"
    for key in STACKCHAN_GBOT_VOICE_BOT_ID STACKCHAN_TOOL_BOT STACKCHAN_TOOL_BOT_ID STACKCHAN_TOKEN; do
        [[ -n $(get "$key") ]] || fail "Missing required key: $key"
    done
    [[ $(get STACKCHAN_TOOL_BOT_ID) == "$(get STACKCHAN_GBOT_VOICE_BOT_ID)" ]] || fail 'Agent IDs must match exactly'
    value=$(get STACKCHAN_TOKEN)
    ((${#value} >= 16)) || fail 'Device token must have at least 16 characters'
    # STACKCHAN_WAKE_WORD=0 is the "button only" fallback. Button-only listen is
    # always on: without a working detector the gateway never forwards audio
    # until the device sends listen (it never falls back to pass-through).
    value=$(get STACKCHAN_WAKE_WORD)
    [[ -n "$value" ]] || value=1
    [[ "$value" == 0 || "$value" == 1 ]] || fail 'STACKCHAN_WAKE_WORD must be 0 or 1'
    put STACKCHAN_WAKE_WORD "$value"
    put STACKCHAN_BUTTON_ONLY_LISTEN 1
    put STACKCHAN_CHILD_PROFILE 1
    put STACKCHAN_VOICE_BACKEND gbot
    put STACKCHAN_WAKE_PHRASE 小白
    put STACKCHAN_PARENT_API 1
    put STACKCHAN_USB_DISABLE 1
    uv_bin=$(get UV_BIN); uv_bin=${uv_bin:-$(command -v uv || printf /opt/homebrew/bin/uv)}
    node_bin=$(get NODE_BIN); node_bin=${node_bin:-$(command -v node || printf /opt/homebrew/bin/node)}
    put UV_BIN "$uv_bin"
    [[ ! -f "$gateway_dir/.env" ]] || printf '%s\n' 'Warning: gateway/.env can add settings via python-dotenv; plist values win for keys set in both.' >&2
}
helper_key() {
    case "$1" in PATH|HOME|STACKCHAN_GBOT_VOICE_BOT_ID|STACKCHAN_TOOL_BOT|STACKCHAN_TOOL_BOT_ID) return 0 ;; *) return 1 ;; esac
}
show_env() {
    local service i value key
    for service in gateway login-helper; do
        printf '%s environment:\n' "$service"
        for ((i=0; i<${#keys[@]}; i++)); do
            key=${keys[i]}; value=${values[i]}
            [[ "$service" != login-helper ]] || helper_key "$key" || continue
            case "$key" in
                STACKCHAN_TOKEN) value="****(${#value} chars)" ;;
                *BOT_ID) value="${value:0:4}****" ;;
                STACKCHAN_TOOL_BOT) value='****' ;;
                PATH|HOME|STACKCHAN_CHILD_PROFILE|STACKCHAN_VOICE_BACKEND|STACKCHAN_WAKE_PHRASE|STACKCHAN_PARENT_API|STACKCHAN_USB_DISABLE|STACKCHAN_BUTTON_ONLY_LISTEN|STACKCHAN_WAKE_WORD) ;;
                *) value='[configured]' ;;
            esac
            printf '%s=%s\n' "$key" "$value"
        done
    done
}
xml() {
    local value=$1
    value=${value//&/\&amp;}; value=${value//</\&lt;}; value=${value//>/\&gt;}
    value=${value//\"/\&quot;}; value=${value//\'/\&apos;}
    printf '%s' "$value"
}
string() { printf '<string>'; xml "$1"; printf '</string>\n'; }
plist() {
    local service=$1 i
    printf '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>\n<key>Label</key>'
    string "local.xiaoya.$service"
    printf '<key>ProgramArguments</key><array>\n'
    if [[ "$service" == gateway ]]; then
        string /bin/bash; string "$gateway_dir/scripts/run_stackchan_gateway_launchd.sh"
    else
        string "$node_bin"; string "$gateway_dir/scripts/gbot_voice_session_bridge.mjs"
    fi
    printf '</array><key>WorkingDirectory</key>'; string "$gateway_dir"
    printf '<key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>10</integer>\n'
    printf '<key>StandardOutPath</key>'; string "$logs/$service.log"
    printf '<key>StandardErrorPath</key>'; string "$logs/$service.log"
    printf '<key>EnvironmentVariables</key><dict>\n'
    for ((i=0; i<${#keys[@]}; i++)); do
        [[ "$service" != login-helper ]] || helper_key "${keys[i]}" || continue
        [[ ${keys[i]} != NODE_BIN ]] || continue
        printf '<key>'; xml "${keys[i]}"; printf '</key>'; string "${values[i]}"
    done
    printf '</dict></dict></plist>\n'
}
run_launchctl() {
    if ((dry)); then printf 'Would run launchctl'; printf ' %q' "$@"; printf '\n'
    else launchctl "$@"; fi
}
case "$command_name" in
    check|install) validate; show_env ;;
esac
case "$command_name" in
    install)
        if ((dry)); then
            printf 'Would write mode 0600 plists in %s; mode 0755 wrapper %s; logs directory mode 0700: %s\n' "$agents_dir" "$wrapper" "$logs"
        else
            umask 077
            mkdir -p "$agents_dir" "$service_home/.stackchan/bin" "$logs"
            chmod 700 "$logs"
            for service in gateway login-helper; do
                plist "${service}" > "$agents_dir/local.xiaoya.$service.plist"
                chmod 600 "$agents_dir/local.xiaoya.$service.plist"
            done
            { printf '#!/usr/bin/env bash\n# SPDX-License-Identifier: MIT\nexec '; printf '%q ' "$uv_bin" run --directory "$gateway_dir" --quiet stackchan-parent; printf '"$@"\n'; } > "$wrapper"
            chmod 755 "$wrapper"
        fi
        for service in gateway login-helper; do
            # Boot out an earlier installation so re-install applies new env.
            run_launchctl bootout "gui/$UID/local.xiaoya.$service" || true
            # bootout finishes asynchronously; retry bootstrap briefly instead of failing.
            if ((dry)); then
                run_launchctl bootstrap "gui/$UID" "$agents_dir/local.xiaoya.$service.plist"
            else
                for attempt in 1 2 3 4 5; do
                    if launchctl bootstrap "gui/$UID" "$agents_dir/local.xiaoya.$service.plist"; then break; fi
                    ((attempt < 5)) || fail "launchctl bootstrap failed for local.xiaoya.$service"
                    sleep 1
                done
            fi
            run_launchctl kickstart "gui/$UID/local.xiaoya.$service"
        done ;;
    uninstall)
        for service in gateway login-helper; do
            run_launchctl bootout "gui/$UID/local.xiaoya.$service" || true
            if ((dry)); then printf 'Would remove %s\n' "$agents_dir/local.xiaoya.$service.plist"
            else rm -f "$agents_dir/local.xiaoya.$service.plist"; fi
        done
        if ((dry)); then printf 'Would remove %s\n' "$wrapper"; else rm -f "$wrapper"; fi ;;
    restart)
        for service in gateway login-helper; do run_launchctl kickstart -k "gui/$UID/local.xiaoya.$service"; done ;;
    status)
        for service in gateway login-helper; do
            if ((dry)); then run_launchctl print "gui/$UID/local.xiaoya.$service"
            else
                # launchctl print includes secrets in EnvironmentVariables: filter it.
                launchctl print "gui/$UID/local.xiaoya.$service" 2>/dev/null | awk '/^[[:space:]]*(state =|pid =|runs =|last exit code =)/' || true
            fi
        done
        if ((dry)); then printf 'Would run stackchan-parent status --text if installed\n'
        elif [[ -x "$wrapper" ]]; then "$wrapper" status --text; fi ;;
esac
