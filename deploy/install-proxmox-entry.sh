#!/bin/bash -p
set -Eeuo pipefail

# Weiterleitbarer Einstiegspunkt für eine Neuinstallation auf Proxmox VE.
# Das nachgelagerte Community-Skript wird vor der Ausführung gegen die beim
# Release-Bau festgeschriebene SHA-256-Prüfsumme geprüft.

base_url="${DPL_BASE_URL:-https://raw.githubusercontent.com/APGW/DPLAcronExportDB/main/dist/internal-repository}"
community_sha256="@@COMMUNITY_SHA256@@"
web_cidrs="${WEB_CIDRS:-}"
ctid="${CTID:-}"
allow_public_cidrs="${DPL_ALLOW_PUBLIC_CIDRS:-0}"
ca_cert="${DPL_CA_CERT:-}"

# Ab hier darf keine vom Aufrufer geerbte Such- oder Shell-Startkonfiguration
# mehr in privilegierte Kindprozesse gelangen. Der äußere Aufruf wird in der
# Dokumentation zusätzlich über `env -i` gestartet.
for function_name in $(builtin compgen -A function); do
  builtin unset -f "$function_name"
done
builtin unset BASH_ENV ENV CDPATH GLOBIGNORE PYTHONPATH PYTHONHOME PYTHONSTARTUP \
  LD_PRELOAD LD_LIBRARY_PATH CURL_HOME CURL_CA_BUNDLE SSL_CERT_FILE SSL_CERT_DIR
builtin export IFS=$' \t\n'
builtin export PATH=/usr/sbin:/usr/bin:/sbin:/bin

readonly BASH_BIN=/bin/bash
readonly CHMOD_BIN=/bin/chmod
readonly CURL_BIN=/usr/bin/curl
readonly ENV_BIN=/usr/bin/env
readonly IP_BIN=/usr/sbin/ip
readonly MKTEMP_BIN=/usr/bin/mktemp
readonly PCT_BIN=/usr/sbin/pct
readonly PYESH_BIN=/usr/bin/pvesh
readonly PYTHON_BIN=/usr/bin/python3
readonly RM_BIN=/bin/rm
readonly SHA256SUM_BIN=/usr/bin/sha256sum
readonly STAT_BIN=/usr/bin/stat

fail() {
  echo "Fehler: $*" >&2
  exit 1
}

[[ "${EUID:-99999}" -eq 0 ]] || fail "Das Skript muss als root auf Proxmox VE laufen."
for required_tool in "$BASH_BIN" "$CHMOD_BIN" "$CURL_BIN" "$ENV_BIN" "$IP_BIN" \
  "$MKTEMP_BIN" "$PCT_BIN" "$PYESH_BIN" "$PYTHON_BIN" "$RM_BIN" "$SHA256SUM_BIN" "$STAT_BIN"; do
  [[ -x "$required_tool" ]] || fail "Erforderliches Systemprogramm fehlt: $required_tool"
  tool_owner="$("$STAT_BIN" -Lc '%u' "$required_tool")"
  tool_mode="$("$STAT_BIN" -Lc '%a' "$required_tool")"
  [[ "$tool_owner" == "0" && $((8#$tool_mode & 022)) -eq 0 ]] \
    || fail "Unsichere Eigentumsrechte am Systemprogramm: $required_tool"
done

base_url="${base_url%/}"
[[ "$base_url" == https://* ]] || fail "DPL_BASE_URL muss HTTPS verwenden."
[[ "$community_sha256" =~ ^[a-f0-9]{64}$ ]] || fail "Die eingebettete Skript-Prüfsumme ist ungültig."
curl_ca_options=()
if [[ -n "$ca_cert" ]]; then
  [[ "$ca_cert" == /* && -f "$ca_cert" && ! -L "$ca_cert" ]] \
    || fail "DPL_CA_CERT muss eine absolute, reguläre CA-Datei auf dem Proxmox-Host sein."
  ca_owner="$("$STAT_BIN" -Lc '%u' "$ca_cert")"
  ca_mode="$("$STAT_BIN" -Lc '%a' "$ca_cert")"
  [[ "$ca_owner" == "0" && $((8#$ca_mode & 022)) -eq 0 ]] \
    || fail "DPL_CA_CERT muss root gehören und darf nicht gruppen-/weltbeschreibbar sein."
  curl_ca_options=(--cacert "$ca_cert")
fi

if [[ -z "$web_cidrs" ]]; then
    default_interface="$($IP_BIN -4 route show default 2>/dev/null | /usr/bin/awk 'NR == 1 {print $5}')"
  default_address=""
  if [[ -n "$default_interface" ]]; then
    default_address="$($IP_BIN -o -4 address show dev "$default_interface" scope global 2>/dev/null | /usr/bin/awk 'NR == 1 {print $4}')"
  fi
  suggested_cidr=""
  if [[ -n "$default_address" ]]; then
    suggested_cidr="$("$PYTHON_BIN" -I - "$default_address" <<'PY'
import ipaddress
import sys

print(ipaddress.ip_interface(sys.argv[1]).network)
PY
)"
  fi

  if [[ -n "$suggested_cidr" ]]; then
    read -r -p "Erlaubte LAN-/VPN-Netze als CIDR [$suggested_cidr]: " web_cidrs
    web_cidrs="${web_cidrs:-$suggested_cidr}"
  else
    read -r -p "Erlaubte LAN-/VPN-Netze als CIDR (z. B. 192.168.1.0/24): " web_cidrs
  fi
fi

[[ "$allow_public_cidrs" == "0" || "$allow_public_cidrs" == "1" ]] \
  || fail "DPL_ALLOW_PUBLIC_CIDRS muss 0 oder 1 sein."
web_cidrs="$("$PYTHON_BIN" -I - "$web_cidrs" "$allow_public_cidrs" <<'PY'
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
        is_company_private = any(network.subnet_of(parent) for parent in private_v4)
    else:
        if network.prefixlen < 32:
            raise SystemExit(1)
        is_company_private = network.subnet_of(private_v6)
    if not is_company_private and not allow_public:
        raise SystemExit(1)
    canonical = str(network)
    if canonical not in normalized:
        normalized.append(canonical)
print(",".join(normalized))
PY
)" || fail "WEB_CIDRS enthält ein ungültiges, spezielles, öffentliches oder zu breites Netz."

if [[ -z "$ctid" ]]; then
  ctid="$($PYESH_BIN get /cluster/nextid 2>/dev/null)"
fi
[[ "$ctid" =~ ^[1-9][0-9]*$ ]] || fail "CTID muss eine positive Zahl sein."
if "$PCT_BIN" status "$ctid" >/dev/null 2>&1; then
  fail "CT $ctid existiert bereits. Für eine Neuinstallation eine freie CTID wählen."
fi

work_dir="$($MKTEMP_BIN -d)"
"$CHMOD_BIN" 0700 "$work_dir"
trap '"$RM_BIN" -rf -- "$work_dir"' EXIT
community_script="$work_dir/dpl-acron.sh"

echo "Lade den geprüften DPL2ACRON-Installer ..."
( ulimit -f 1024; "$CURL_BIN" --disable --fail --silent --show-error --location \
  --proto '=https' --proto-redir '=https' --tlsv1.2 --connect-timeout 10 \
  --max-time 60 --speed-limit 1024 --speed-time 15 --max-filesize 1048576 \
  "${curl_ca_options[@]}" --output "$community_script" "$base_url/ct/dpl-acron.sh" )
printf '%s  %s\n' "$community_sha256" "$community_script" | "$SHA256SUM_BIN" --check --status - \
  || fail "SHA-256-Prüfung des Installers fehlgeschlagen; es wurde nichts ausgeführt."

echo "Installiere DPL2ACRON in CT $ctid; Webzugriff aus: $web_cidrs"
clean_environment=(
  "$ENV_BIN" -i
  HOME=/root
  PATH=/usr/sbin:/usr/bin:/sbin:/bin
  "DPL_BASE_URL=$base_url"
  "CTID=$ctid"
  "WEB_CIDRS=$web_cidrs"
  "DPL_ALLOW_PUBLIC_CIDRS=$allow_public_cidrs"
)
if [[ -n "$ca_cert" ]]; then
  clean_environment+=("DPL_CA_CERT=$ca_cert")
fi
if [[ -n "${TERM:-}" ]]; then
  clean_environment+=("TERM=$TERM")
fi
"${clean_environment[@]}" "$BASH_BIN" -p --noprofile --norc "$community_script"
