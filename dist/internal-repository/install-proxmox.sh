#!/usr/bin/env bash
set -Eeuo pipefail

# Weiterleitbarer Einstiegspunkt für eine Neuinstallation auf Proxmox VE.
# Das nachgelagerte Community-Skript wird vor der Ausführung gegen die beim
# Release-Bau festgeschriebene SHA-256-Prüfsumme geprüft.

base_url="${DPL_BASE_URL:-https://raw.githubusercontent.com/APGW/DPLAcronExportDB/main/dist/internal-repository}"
community_sha256="557d7a614456778e716132f616f8c943841d772c9ae49aba06786a27adcc58e2"
web_cidrs="${WEB_CIDRS:-}"
ctid="${CTID:-}"

fail() {
  echo "Fehler: $*" >&2
  exit 1
}

[[ "${EUID:-$(id -u)}" -eq 0 ]] || fail "Das Skript muss als root auf Proxmox VE laufen."
command -v pct >/dev/null 2>&1 || fail "Proxmox VE wurde nicht erkannt (pct fehlt)."
command -v pvesh >/dev/null 2>&1 || fail "Proxmox VE wurde nicht erkannt (pvesh fehlt)."
command -v curl >/dev/null 2>&1 || fail "curl fehlt auf dem Proxmox-Host."
command -v python3 >/dev/null 2>&1 || fail "python3 fehlt auf dem Proxmox-Host."

base_url="${base_url%/}"
[[ "$base_url" == https://* ]] || fail "DPL_BASE_URL muss HTTPS verwenden."
[[ "$community_sha256" =~ ^[a-f0-9]{64}$ ]] || fail "Die eingebettete Skript-Prüfsumme ist ungültig."

if [[ -z "$web_cidrs" ]]; then
  default_interface="$(ip -4 route show default 2>/dev/null | awk 'NR == 1 {print $5}')"
  default_address=""
  if [[ -n "$default_interface" ]]; then
    default_address="$(ip -o -4 address show dev "$default_interface" scope global 2>/dev/null | awk 'NR == 1 {print $4}')"
  fi
  suggested_cidr=""
  if [[ -n "$default_address" ]]; then
    suggested_cidr="$(python3 - "$default_address" <<'PY'
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

python3 - "$web_cidrs" <<'PY' || fail "WEB_CIDRS enthält ein ungültiges oder zu breites Netz."
import ipaddress
import sys

values = [item.strip() for item in sys.argv[1].split(",") if item.strip()]
if not values:
    raise SystemExit(1)
for value in values:
    network = ipaddress.ip_network(value, strict=False)
    if network.prefixlen == 0:
        raise SystemExit(1)
PY

if [[ -z "$ctid" ]]; then
  ctid="$(pvesh get /cluster/nextid 2>/dev/null)"
fi
[[ "$ctid" =~ ^[1-9][0-9]*$ ]] || fail "CTID muss eine positive Zahl sein."
if pct status "$ctid" >/dev/null 2>&1; then
  fail "CT $ctid existiert bereits. Für eine Neuinstallation eine freie CTID wählen."
fi

work_dir="$(mktemp -d)"
chmod 0700 "$work_dir"
trap 'rm -rf -- "$work_dir"' EXIT
community_script="$work_dir/dpl-acron.sh"

echo "Lade den geprüften DPL2ACRON-Installer ..."
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
  --output "$community_script" "$base_url/ct/dpl-acron.sh"
printf '%s  %s\n' "$community_sha256" "$community_script" | sha256sum --check --status - \
  || fail "SHA-256-Prüfung des Installers fehlgeschlagen; es wurde nichts ausgeführt."

echo "Installiere DPL2ACRON in CT $ctid; Webzugriff aus: $web_cidrs"
export DPL_BASE_URL="$base_url"
export CTID="$ctid"
export WEB_CIDRS="$web_cidrs"
exec bash "$community_script"
