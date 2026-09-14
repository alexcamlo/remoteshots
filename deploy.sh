#!/usr/bin/env bash
# Deploy the current server.py without changing the service unit or environment.
set -Eeuo pipefail

readonly SERVICE=remoteshots.service
readonly SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SOURCE_FILE="$SCRIPT_DIR/server.py"
readonly TARGET_FILE="${HOME:?HOME is not set}/.local/lib/remoteshots/server.py"
readonly ENV_FILE="${HOME:?HOME is not set}/.config/remoteshots/environment"

show_journal() {
    printf '\n--- recent %s journal ---\n' "$SERVICE" >&2
    journalctl --user -u "$SERVICE" -n 40 --no-pager >&2 || true
}

fail() {
    printf 'deploy: error: %s\n' "$*" >&2
    show_journal
    exit 1
}

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
    fail 'run as the service user, not root'
fi

command -v systemctl >/dev/null 2>&1 || fail 'systemctl is required'
command -v journalctl >/dev/null 2>&1 || fail 'journalctl is required'
command -v install >/dev/null 2>&1 || fail 'install is required'
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN=python3
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN=python
else
    fail 'python3 (or python) is required'
fi

[[ -f "$SOURCE_FILE" ]] || fail "missing $SOURCE_FILE"
[[ -r "$ENV_FILE" ]] || fail "missing $ENV_FILE; install and configure remoteshots.env.example first"

# Read only the two simple assignments needed for the health check. Do not source
# the environment file: it is configuration, not trusted shell code.
bind=''
port=''
while IFS= read -r line || [[ -n "$line" ]]; do
    # Ignore blank lines and comments, including lines with leading whitespace.
    line="${line#"${line%%[![:space:]]*}"}"
    [[ -z "$line" || ${line:0:1} == '#' ]] && continue
    case "$line" in
        REMOTESHOTS_BIND=*) bind=${line#*=} ;;
        REMOTESHOTS_PORT=*) port=${line#*=} ;;
    esac
done < "$ENV_FILE"

# The checked-in example uses unquoted values. Accept matching surrounding
# quotes without evaluating escapes or other shell syntax.
if [[ $bind == \"*\" && $bind == *\" ]]; then bind=${bind:1:${#bind}-2}; fi
if [[ $bind == \'*\' && $bind == *\' ]]; then bind=${bind:1:${#bind}-2}; fi
if [[ $port == \"*\" && $port == *\" ]]; then port=${port:1:${#port}-2}; fi
if [[ $port == \'*\' && $port == *\' ]]; then port=${port:1:${#port}-2}; fi
[[ -n "$bind" ]] || fail "REMOTESHOTS_BIND is missing from $ENV_FILE"
[[ -n "$port" ]] || fail "REMOTESHOTS_PORT is missing from $ENV_FILE"

printf 'Checking %s...\n' "$SOURCE_FILE"
"$PYTHON_BIN" -m py_compile -- "$SOURCE_FILE" || fail 'Python compile check failed'

printf 'Installing %s -> %s...\n' "$SOURCE_FILE" "$TARGET_FILE"
install -Dm755 -- "$SOURCE_FILE" "$TARGET_FILE" || fail 'install failed'

printf 'Restarting %s...\n' "$SERVICE"
systemctl --user restart "$SERVICE" || fail "could not restart $SERVICE"

state=''
for _ in {1..20}; do
    state=$(systemctl --user is-active "$SERVICE" 2>/dev/null || true)
    case "$state" in
        active) break ;;
        failed|inactive|dead) fail "$SERVICE is $state after restart" ;;
    esac
    sleep 1
done
[[ "$state" == active ]] || fail "$SERVICE did not become active (last state: ${state:-unknown})"
printf '%s is active.\n' "$SERVICE"

printf 'Checking http://%s:%s/...\n' "$bind" "$port"
health_check() {
    "$PYTHON_BIN" - "$bind" "$port" <<'PY'
import http.client
import ipaddress
import sys

bind, port_text = sys.argv[1:]
try:
    address = ipaddress.ip_address(bind)
    port = int(port_text)
except ValueError as error:
    raise SystemExit(f"invalid configured bind or port: {error}")

tailscale = ipaddress.ip_network("100.64.0.0/10")
if (
    address.version != 4
    or address.is_unspecified
    or address.is_loopback
    or not (address.is_private or address in tailscale)
):
    raise SystemExit(f"refusing non-private health-check address: {address}")
if not 1 <= port <= 65535:
    raise SystemExit(f"invalid port: {port}")

connection = http.client.HTTPConnection(bind, port, timeout=5)
try:
    connection.request("GET", "/")
    response = connection.getresponse()
    response.read(1024)
    if response.status != 200:
        raise SystemExit(f"HTTP status {response.status} {response.reason}")
finally:
    connection.close()
PY
}

health_error=''
for _ in {1..10}; do
    if health_error=$(health_check 2>&1); then
        health_error=''
        break
    fi
    sleep 1
done
if [[ -n "$health_error" ]]; then
    printf 'deploy: last health-check error: %s\n' "$health_error" >&2
    fail 'health check failed'
fi

printf 'Deployment complete.\n'
