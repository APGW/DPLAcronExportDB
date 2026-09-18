#!/usr/bin/env bash

# Community-Scripts-CT-Skript. Die gemeinsame Engine bleibt upstream;
# ct/ und install/ werden aus dem internen DPL-Repository geladen.

APP="dpl-acron"
var_tags="database;intranet;automation"
var_cpu="2"
var_ram="2048"
var_disk="8"
var_os="debian"
var_version="13"
var_arm64="${var_arm64:-no}" # Python/MariaDB-Paketstand bisher nur auf amd64 verifiziert
var_unprivileged="1"

export DPL_APP_VERSION="3.4.0"
export DPL_BUNDLE_SHA256="c74f2e095eedc7ebd8e9868d5f381d8e663dbc3028c02102d2cee12389690669"
export DPL_BASE_URL="${DPL_BASE_URL:-}"
export DPL_ALLOW_HTTP="${DPL_ALLOW_HTTP:-0}"
export DPL_CA_CERT="${DPL_CA_CERT:-}"
export WEB_CIDRS="${WEB_CIDRS:-}"

fail_early() {
  echo "Fehler: $*" >&2
  exit 1
}

[[ -n "$DPL_BASE_URL" ]] || fail_early "DPL_BASE_URL muss auf das interne Repository zeigen."
DPL_BASE_URL="${DPL_BASE_URL%/}"
export DPL_BASE_URL
if [[ "$DPL_BASE_URL" == http://* && "$DPL_ALLOW_HTTP" != "1" ]]; then
  fail_early "Für unverschlüsseltes Test-HTTP muss DPL_ALLOW_HTTP=1 gesetzt sein."
fi
[[ "$DPL_BASE_URL" == https://* || "$DPL_BASE_URL" == http://* ]] \
  || fail_early "DPL_BASE_URL muss mit https:// oder http:// beginnen."
[[ -n "$WEB_CIDRS" ]] || fail_early "WEB_CIDRS muss die erlaubten Intranet-/VPN-Netze enthalten."

# Das aktuelle Community-Scripts-Core lädt das zu APP gehörende Installskript
# aus COMMUNITY_SCRIPTS_URL/install/. Damit kann das Anwendungsmodul intern
# bleiben, während die standardisierte LXC-Engine upstream gepflegt wird.
export COMMUNITY_SCRIPTS_URL="$DPL_BASE_URL"
export COMMUNITY_SCRIPTS_CORE_URL="${COMMUNITY_SCRIPTS_CORE_URL:-https://raw.githubusercontent.com/community-scripts/core/main}"
source <(curl -fsSL "$COMMUNITY_SCRIPTS_CORE_URL/core/build.func")

header_info "$APP"
variables
color
catch_errors

function update_script() {
  header_info
  check_container_storage
  check_container_resources
  [[ -f /opt/dpl-acron/app.py ]] || {
    msg_error "Keine DPL-Acron-Installation gefunden."
    exit 1
  }

  local update_dir update_bundle
  update_dir="$(mktemp -d)"
  update_bundle="$update_dir/dpl-acron-debian13.tar.gz"
  msg_info "Lade und prüfe DPL Acron ${DPL_APP_VERSION}"
  if [[ "$DPL_BASE_URL" == https://* ]]; then
    curl -fsSL --proto '=https' --tlsv1.2 \
      --output "$update_bundle" \
      "${DPL_BASE_URL}/releases/${DPL_APP_VERSION}/dpl-acron-debian13.tar.gz"
  else
    curl -fsSL --proto '=http' \
      --output "$update_bundle" \
      "${DPL_BASE_URL}/releases/${DPL_APP_VERSION}/dpl-acron-debian13.tar.gz"
  fi
  printf '%s  %s\n' "$DPL_BUNDLE_SHA256" "$update_bundle" | sha256sum --check --status - || {
    msg_error "SHA-256-Prüfung des DPL-Releases fehlgeschlagen."
    rm -rf -- "$update_dir"
    exit 1
  }
  mkdir -p "$update_dir/source"
  tar -xzf "$update_bundle" -C "$update_dir/source"
  bash "$update_dir/source/deploy/install-debian13.sh"
  rm -rf -- "$update_dir"
  msg_ok "DPL Acron ${DPL_APP_VERSION} aktualisiert"
  exit 0
}

validate_cidrs() {
  local item
  IFS=',' read -r -a items <<< "$WEB_CIDRS"
  for item in "${items[@]}"; do
    [[ "$item" =~ ^[0-9A-Fa-f:.]+/[0-9]{1,3}$ ]] || {
      msg_error "Ungültiges Netz in WEB_CIDRS: $item"
      exit 1
    }
  done
}

apply_intranet_firewall() {
  local firewall_file net0 item
  firewall_file="$(mktemp)"
  {
    echo "[OPTIONS]"
    echo "enable: 1"
    echo "policy_in: DROP"
    echo "policy_out: ACCEPT"
    echo
    echo "[RULES]"
    echo "IN ACCEPT -p icmp -log nolog"
    echo "IN ACCEPT -p udp -sport 67 -dport 68 -log nolog"
    IFS=',' read -r -a items <<< "$WEB_CIDRS"
    for item in "${items[@]}"; do
      echo "IN ACCEPT -source $item -p tcp -dport 8765 -log nolog"
    done
  } > "$firewall_file"
  cp -- "$firewall_file" "/etc/pve/firewall/${CTID}.fw"
  rm -f -- "$firewall_file"

  net0="$(pct config "$CTID" | sed -n 's/^net0: //p')"
  if [[ "$net0" == *",firewall=0"* ]]; then
    net0="${net0/,firewall=0/,firewall=1}"
    pct set "$CTID" --net0 "$net0" >/dev/null
  elif [[ "$net0" != *",firewall=1"* ]]; then
    pct set "$CTID" --net0 "$net0,firewall=1" >/dev/null
  fi
}

validate_cidrs
start
build_container
apply_intranet_firewall
description

msg_ok "Installation abgeschlossen"
echo -e "${APP} ist erreichbar unter:\n${BL}http://${IP}:8765/${CL}"
echo -e "Datenbank und tägliche Sicherung laufen im CT ${CTID}."
