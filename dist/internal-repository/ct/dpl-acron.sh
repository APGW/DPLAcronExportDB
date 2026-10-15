#!/bin/bash -p
set -Eeuo pipefail

# Internes, Community-Scripts-kompatibles CT-Einstiegsskript. Es führt keinen
# veränderlichen Fremdcode als Proxmox-root aus: Erst das projektinterne Release
# wird geladen und per eingebettetem SHA-256 geprüft, danach startet der darin
# enthaltene LXC-Installer. Das Administratorkennwort wird erst dort abgefragt.

# Ein versehentlich geerbtes Kennwort darf keinem Prüf-, Download- oder
# Installationsprozess des Wrappers zugänglich sein.
APP="dpl-acron"
DPL_APP_VERSION="3.7.4"
DPL_BUNDLE_SHA256="c7e7366ae8c15091136f7316a540c74e3eb95ddbb4493fc4748d89b42c44a3da"
DPL_BASE_URL="${DPL_BASE_URL:-}"
DPL_ALLOW_HTTP="${DPL_ALLOW_HTTP:-0}"
DPL_CA_CERT="${DPL_CA_CERT:-}"
WEB_CIDRS="${WEB_CIDRS:-}"
CTID="${CTID:-}"
DPL_ALLOW_PUBLIC_CIDRS="${DPL_ALLOW_PUBLIC_CIDRS:-0}"

for function_name in $(builtin compgen -A function); do
  builtin unset -f "$function_name"
done
builtin unset DPL_ADMIN_PASSWORD BASH_ENV ENV CDPATH GLOBIGNORE PYTHONPATH PYTHONHOME PYTHONSTARTUP \
  LD_PRELOAD LD_LIBRARY_PATH CURL_HOME CURL_CA_BUNDLE SSL_CERT_FILE SSL_CERT_DIR TMPDIR
builtin export IFS=$' \t\n'
builtin export PATH=/usr/sbin:/usr/bin:/sbin:/bin

readonly BASH_BIN=/bin/bash
readonly CURL_BIN=/usr/bin/curl
readonly ENV_BIN=/usr/bin/env
readonly MKDIR_BIN=/usr/bin/mkdir
readonly MKTEMP_BIN=/usr/bin/mktemp
readonly PCT_BIN=/usr/sbin/pct
readonly PYESH_BIN=/usr/bin/pvesh
readonly PYTHON_BIN=/usr/bin/python3
readonly RM_BIN=/bin/rm
readonly SHA256SUM_BIN=/usr/bin/sha256sum
readonly STAT_BIN=/usr/bin/stat
readonly TAR_BIN=/usr/bin/tar

fail() {
  echo "Fehler: $*" >&2
  exit 1
}

[[ "${EUID:-99999}" -eq 0 ]] || fail "Das Skript muss als root auf Proxmox VE laufen."
update_mode=0
if [[ ! -x "$PCT_BIN" ]]; then
  [[ -f /opt/dpl-acron/app.py ]] \
    || fail "Weder Proxmox VE noch eine vorhandene DPL2ACRON-Installation wurde erkannt."
  update_mode=1
fi
[[ -x "$CURL_BIN" && -x "$PYTHON_BIN" && -x "$SHA256SUM_BIN" ]] \
  || fail "Erforderliche Systemprogramme fehlen."
if [[ "$update_mode" -eq 1 && -z "$DPL_BASE_URL" && -r /etc/dpl-acron/update.env ]]; then
  while IFS='=' read -r key value; do
    case "$key" in
      DPL_BASE_URL) DPL_BASE_URL="$value" ;;
      DPL_ALLOW_HTTP) DPL_ALLOW_HTTP="$value" ;;
      DPL_CA_CERT) DPL_CA_CERT="$value" ;;
    esac
  done < /etc/dpl-acron/update.env
fi
[[ -n "$DPL_BASE_URL" ]] || fail "DPL_BASE_URL muss auf das interne Repository zeigen."
DPL_BASE_URL="${DPL_BASE_URL%/}"
if [[ "$DPL_BASE_URL" == http://* && "$DPL_ALLOW_HTTP" != "1" ]]; then
  fail "Unverschlüsseltes Repository-HTTP erfordert ausdrücklich DPL_ALLOW_HTTP=1."
fi
[[ "$DPL_BASE_URL" == https://* || "$DPL_BASE_URL" == http://* ]] \
  || fail "DPL_BASE_URL muss mit https:// oder http:// beginnen."
if [[ "$update_mode" -eq 0 ]]; then
  [[ -n "$WEB_CIDRS" ]] || fail "WEB_CIDRS muss die erlaubten Intranet-/VPN-Netze enthalten."
fi
[[ "$DPL_ALLOW_PUBLIC_CIDRS" == "0" || "$DPL_ALLOW_PUBLIC_CIDRS" == "1" ]] \
  || fail "DPL_ALLOW_PUBLIC_CIDRS muss 0 oder 1 sein."
curl_ca_options=()
if [[ -n "$DPL_CA_CERT" ]]; then
  [[ "$DPL_CA_CERT" == /* && -f "$DPL_CA_CERT" && ! -L "$DPL_CA_CERT" ]] \
    || fail "DPL_CA_CERT muss eine absolute, reguläre CA-Datei auf dem Proxmox-Host sein."
  ca_mode="$(/usr/bin/stat -c '%a' -- "$DPL_CA_CERT")"
  [[ "$(/usr/bin/stat -c '%u' -- "$DPL_CA_CERT")" == "0" ]] \
    && (( (8#$ca_mode & 8#022) == 0 )) \
    || fail "DPL_CA_CERT muss root gehören und darf nicht gruppen-/weltbeschreibbar sein."
  curl_ca_options=(--cacert "$DPL_CA_CERT")
fi

if [[ "$update_mode" -eq 0 ]]; then
WEB_CIDRS="$("$PYTHON_BIN" -I - "$WEB_CIDRS" "$DPL_ALLOW_PUBLIC_CIDRS" <<'PY'
import ipaddress
import sys

raw_values = sys.argv[1].split(",")
if not raw_values or any(not item.strip() for item in raw_values):
    raise SystemExit(1)
values = [item.strip() for item in raw_values]
allow_public = sys.argv[2] == "1"
private_v4 = [ipaddress.ip_network(value) for value in ("10.0.0.0/8", "100.64.0.0/10", "172.16.0.0/12", "192.168.0.0/16")]
private_v6 = ipaddress.ip_network("fc00::/7")
normalized = []
for value in values:
    network = ipaddress.ip_network(value, strict=True)
    if network.is_loopback or network.is_link_local or network.is_multicast or network.is_unspecified or network.is_reserved:
        raise SystemExit(1)
    if network.version == 4:
        if network.prefixlen < 8:
            raise SystemExit(1)
        private = any(network.subnet_of(parent) for parent in private_v4)
    else:
        if network.prefixlen < 32:
            raise SystemExit(1)
        private = network.subnet_of(private_v6)
    if not private and not allow_public:
        raise SystemExit(1)
    canonical = str(network)
    if canonical not in normalized:
        normalized.append(canonical)
print(",".join(normalized))
PY
)" || fail "WEB_CIDRS enthält ein ungültiges, spezielles, öffentliches oder zu breites Netz."
fi

[[ -d /root && ! -L /root ]] || fail "/root ist kein sicheres temporäres Stammverzeichnis."
root_owner="$($STAT_BIN -Lc '%u' /root)"
root_mode="$($STAT_BIN -Lc '%a' /root)"
[[ "$root_owner" == "0" && $((8#$root_mode & 022)) -eq 0 ]] \
  || fail "/root muss root gehören und darf nicht gruppen-/weltbeschreibbar sein."
work_dir="$($MKTEMP_BIN -d -p /root .dpl2acron-release.XXXXXX)"
/bin/chmod 0700 "$work_dir"
trap '"$RM_BIN" -rf -- "$work_dir"' EXIT
bundle="$work_dir/dpl-acron-debian13.tar.gz"
source_dir="$work_dir/source"
bundle_url="$DPL_BASE_URL/releases/$DPL_APP_VERSION/dpl-acron-debian13.tar.gz"

echo "Lade und prüfe DPL2ACRON $DPL_APP_VERSION ..."
if [[ "$bundle_url" == https://* ]]; then
  ( ulimit -f 25600; "$CURL_BIN" --disable --fail --silent --show-error --location \
    --proto '=https' --proto-redir '=https' --tlsv1.2 --connect-timeout 10 \
    --max-time 300 --speed-limit 1024 --speed-time 30 --max-filesize 26214400 \
    "${curl_ca_options[@]}" --output "$bundle" "$bundle_url" )
else
  ( ulimit -f 25600; "$CURL_BIN" --disable --fail --silent --show-error --location \
    --proto '=http' --proto-redir '=http' --connect-timeout 10 --max-time 300 \
    --speed-limit 1024 --speed-time 30 --max-filesize 26214400 \
    --output "$bundle" "$bundle_url" )
fi
printf '%s  %s\n' "$DPL_BUNDLE_SHA256" "$bundle" | "$SHA256SUM_BIN" --check --status - \
  || fail "SHA-256-Prüfung des Releases fehlgeschlagen; es wurde nichts ausgeführt."
"$MKDIR_BIN" -m 0700 "$source_dir"
"$TAR_BIN" -xzf "$bundle" -C "$source_dir"
installer="$source_dir/deploy/proxmox-install-lxc.sh"
[[ -f "$installer" ]] || fail "Der geprüfte LXC-Installer fehlt im Release."

if [[ "$update_mode" -eq 1 ]]; then
  echo "Aktualisiere die vorhandene Installation auf $DPL_APP_VERSION ..."
  "$ENV_BIN" -i HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    "DPL_UPDATE_BASE_URL=$DPL_BASE_URL" "DPL_UPDATE_ALLOW_HTTP=$DPL_ALLOW_HTTP" \
    "DPL_UPDATE_CA_CERT_SOURCE=$DPL_CA_CERT" \
    "$BASH_BIN" -p --noprofile --norc "$source_dir/deploy/install-debian13.sh"
  exit
fi

CTID="${CTID:-$($PYESH_BIN get /cluster/nextid 2>/dev/null)}"
clean_environment=(
  "$ENV_BIN" -i HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin
  "CTID=$CTID" "WEB_CIDRS=$WEB_CIDRS"
  "DPL_ALLOW_PUBLIC_CIDRS=$DPL_ALLOW_PUBLIC_CIDRS"
  "DPL_UPDATE_BASE_URL=$DPL_BASE_URL" "DPL_UPDATE_ALLOW_HTTP=$DPL_ALLOW_HTTP"
  "BUNDLE_PATH=$bundle" "BUNDLE_SHA256=$DPL_BUNDLE_SHA256"
)
if [[ -n "$DPL_CA_CERT" ]]; then
  clean_environment+=("DPL_CA_CERT=$DPL_CA_CERT")
fi
if [[ -n "${TERM:-}" ]]; then
  clean_environment+=("TERM=$TERM")
fi
"${clean_environment[@]}" "$BASH_BIN" -p --noprofile --norc "$installer"
