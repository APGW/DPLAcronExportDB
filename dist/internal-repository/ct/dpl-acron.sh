#!/usr/bin/env bash
set -Eeuo pipefail

# Internes, Community-Scripts-kompatibles CT-Einstiegsskript. Es führt keinen
# veränderlichen Fremdcode als Proxmox-root aus: Erst das projektinterne Release
# wird geladen und per eingebettetem SHA-256 geprüft, danach startet der darin
# enthaltene LXC-Installer. Das Administratorkennwort wird erst dort abgefragt.

# Ein versehentlich geerbtes Kennwort darf keinem Prüf-, Download- oder
# Installationsprozess des Wrappers zugänglich sein.
unset DPL_ADMIN_PASSWORD

APP="dpl-acron"
DPL_APP_VERSION="3.7.1"
DPL_BUNDLE_SHA256="64cd4a6859a81c0c5c3ab7f132a1830343849c2f56f72f8178379cdde3b90bd2"
DPL_BASE_URL="${DPL_BASE_URL:-}"
DPL_ALLOW_HTTP="${DPL_ALLOW_HTTP:-0}"
DPL_CA_CERT="${DPL_CA_CERT:-}"
WEB_CIDRS="${WEB_CIDRS:-}"

fail() {
  echo "Fehler: $*" >&2
  exit 1
}

[[ "${EUID:-$(id -u)}" -eq 0 ]] || fail "Das Skript muss als root auf Proxmox VE laufen."
update_mode=0
if ! command -v pct >/dev/null 2>&1; then
  [[ -f /opt/dpl-acron/app.py ]] \
    || fail "Weder Proxmox VE noch eine vorhandene DPL2ACRON-Installation wurde erkannt."
  update_mode=1
fi
command -v curl >/dev/null 2>&1 || fail "curl fehlt auf dem Proxmox-Host."
if [[ "$update_mode" -eq 1 && -z "$DPL_BASE_URL" && -r /etc/dpl-acron/update.env ]]; then
  while IFS='=' read -r key value; do
    case "$key" in
      DPL_BASE_URL) DPL_BASE_URL="$value" ;;
      DPL_ALLOW_HTTP) DPL_ALLOW_HTTP="$value" ;;
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
curl_ca_options=()
if [[ -n "$DPL_CA_CERT" ]]; then
  [[ "$DPL_CA_CERT" == /* && -f "$DPL_CA_CERT" && ! -L "$DPL_CA_CERT" ]] \
    || fail "DPL_CA_CERT muss eine absolute, reguläre CA-Datei auf dem Proxmox-Host sein."
  curl_ca_options=(--cacert "$DPL_CA_CERT")
fi

if [[ "$update_mode" -eq 0 ]]; then
python3 - "$WEB_CIDRS" <<'PY' || fail "WEB_CIDRS enthält ein ungültiges oder zu breites Netz."
import ipaddress
import sys

for raw in sys.argv[1].split(","):
    network = ipaddress.ip_network(raw.strip(), strict=False)
    if network.prefixlen == 0:
        raise SystemExit(1)
PY
fi

work_dir="$(mktemp -d)"
chmod 0700 "$work_dir"
trap 'rm -rf -- "$work_dir"' EXIT
bundle="$work_dir/dpl-acron-debian13.tar.gz"
source_dir="$work_dir/source"
bundle_url="$DPL_BASE_URL/releases/$DPL_APP_VERSION/dpl-acron-debian13.tar.gz"

echo "Lade und prüfe DPL2ACRON $DPL_APP_VERSION ..."
if [[ "$bundle_url" == https://* ]]; then
  curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 "${curl_ca_options[@]}" \
    --output "$bundle" "$bundle_url"
else
  curl --fail --silent --show-error --location --proto '=http' \
    --output "$bundle" "$bundle_url"
fi
printf '%s  %s\n' "$DPL_BUNDLE_SHA256" "$bundle" | sha256sum --check --status - \
  || fail "SHA-256-Prüfung des Releases fehlgeschlagen; es wurde nichts ausgeführt."
mkdir -m 0700 "$source_dir"
tar -xzf "$bundle" -C "$source_dir"
installer="$source_dir/deploy/proxmox-install-lxc.sh"
[[ -f "$installer" ]] || fail "Der geprüfte LXC-Installer fehlt im Release."

if [[ "$update_mode" -eq 1 ]]; then
  echo "Aktualisiere die vorhandene Installation auf $DPL_APP_VERSION ..."
  exec bash "$source_dir/deploy/install-debian13.sh"
fi

export CTID="${CTID:-$(pvesh get /cluster/nextid 2>/dev/null)}"
export WEB_CIDRS
export DPL_UPDATE_BASE_URL="$DPL_BASE_URL"
export DPL_UPDATE_ALLOW_HTTP="$DPL_ALLOW_HTTP"
export BUNDLE_PATH="$bundle"
export BUNDLE_SHA256="$DPL_BUNDLE_SHA256"
exec bash "$installer"
