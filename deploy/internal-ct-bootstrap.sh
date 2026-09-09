#!/usr/bin/env bash
set -Eeuo pipefail

# Diese Datei ist eine Build-Vorlage. deploy/build-internal-repository.ps1
# ersetzt die Platzhalter und veröffentlicht das fertige Skript unter
# dist/internal-repository/ct/dpl-acron.sh.

app_version="@@APP_VERSION@@"
bundle_sha256="@@BUNDLE_SHA256@@"
installer_sha256="@@INSTALLER_SHA256@@"
base_url="${DPL_BASE_URL:-}"
ca_certificate="${DPL_CA_CERT:-}"
allow_http="${DPL_ALLOW_HTTP:-0}"
work_dir=""

fail() {
  echo "Fehler: $*" >&2
  exit 1
}

cleanup() {
  if [[ -n "$work_dir" && -d "$work_dir" ]]; then
    rm -rf -- "$work_dir"
  fi
}
trap cleanup EXIT

if [[ "${EUID}" -ne 0 ]] || ! command -v pct >/dev/null 2>&1; then
  fail "Dieses Skript muss in der Proxmox-VE-Shell als root laufen."
fi
[[ -n "$base_url" ]] || fail "DPL_BASE_URL ist nicht gesetzt."
base_url="${base_url%/}"
command -v curl >/dev/null 2>&1 || fail "curl fehlt auf dem Proxmox-Host."
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum fehlt auf dem Proxmox-Host."

curl_options=(--fail --silent --show-error --location)
if [[ "$base_url" == https://* ]]; then
  curl_options+=(--proto '=https' --tlsv1.2)
  if [[ -n "$ca_certificate" ]]; then
    [[ -f "$ca_certificate" ]] || fail "DPL_CA_CERT wurde nicht gefunden: $ca_certificate"
    curl_options+=(--cacert "$ca_certificate")
  fi
elif [[ "$base_url" == http://* && "$allow_http" == "1" ]]; then
  curl_options+=(--proto '=http')
else
  fail "HTTPS ist erforderlich; lokales HTTP muss ausdrücklich mit DPL_ALLOW_HTTP=1 erlaubt werden."
fi

work_dir="$(mktemp -d)"
installer_file="$work_dir/proxmox-install-lxc.sh"
bundle_file="$work_dir/dpl-acron-debian13.tar.gz"
release_url="$base_url/releases/$app_version"

echo "Lade DPL Acron Exportassistent $app_version vom internen Repository ..."
curl "${curl_options[@]}" --output "$installer_file" "$release_url/proxmox-install-lxc.sh"
curl "${curl_options[@]}" --output "$bundle_file" "$release_url/dpl-acron-debian13.tar.gz"

printf '%s  %s\n' "$installer_sha256" "$installer_file" | sha256sum --check --status - \
  || fail "Prüfsumme des Proxmox-Installers stimmt nicht."
printf '%s  %s\n' "$bundle_sha256" "$bundle_file" | sha256sum --check --status - \
  || fail "Prüfsumme des Release-Archivs stimmt nicht."

echo "Prüfsummen gültig. Starte die LXC-Installation ..."
BUNDLE_PATH="$bundle_file" \
BUNDLE_SHA256="$bundle_sha256" \
bash "$installer_file"
