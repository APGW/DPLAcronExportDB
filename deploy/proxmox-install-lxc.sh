#!/usr/bin/env bash
set -Eeuo pipefail

admin_password="${DPL_ADMIN_PASSWORD:-}"
unset DPL_ADMIN_PASSWORD

# Vollständige Installation eines neuen Debian-13-LXC auf einem Proxmox-VE-Host.
# Pflichtangaben:
#   CTID=3072 WEB_CIDRS="192.168.133.0/24,10.20.0.0/16" \
#   bash proxmox-install-lxc.sh
# Alternativ zum lokalen Archiv:
#   BUNDLE_URL=https://intern.example/dpl-acron-debian13.tar.gz \
#   BUNDLE_SHA256=<64-zeichen> ... bash proxmox-install-lxc.sh

if [[ "${EUID}" -ne 0 ]] || ! command -v pct >/dev/null 2>&1; then
  echo "Dieses Skript muss als root auf einem Proxmox-VE-Host laufen." >&2
  exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ctid="${CTID:-}"
ct_hostname="${CT_HOSTNAME:-dpl-acron}"
root_storage="${ROOT_STORAGE:-local-lvm}"
template_storage="${TEMPLATE_STORAGE:-local}"
bridge="${BRIDGE:-vmbr0}"
ip_config="${IP_CONFIG:-dhcp}"
gateway="${GATEWAY:-}"
web_cidrs="${WEB_CIDRS:-}"
ssh_cidrs="${SSH_CIDRS:-}"
ssh_public_keys="${SSH_PUBLIC_KEYS:-}"
bundle_path="${BUNDLE_PATH:-}"
bundle_url="${BUNDLE_URL:-}"
bundle_sha256="${BUNDLE_SHA256:-}"
resume_existing="${RESUME_EXISTING:-0}"
cores="${CORES:-2}"
memory="${MEMORY_MB:-2048}"
swap="${SWAP_MB:-512}"
rootfs_size="${ROOTFS_GB:-8}"
admin_username="${DPL_ADMIN_USERNAME:-admin}"
update_base_url="${DPL_UPDATE_BASE_URL:-}"
update_allow_http="${DPL_UPDATE_ALLOW_HTTP:-0}"
work_dir="$(mktemp -d)"
firewall_temp=""
container_password_file=""

cleanup() {
  rm -rf -- "$work_dir"
  if [[ -n "$firewall_temp" ]]; then
    rm -f -- "$firewall_temp"
  fi
  if [[ -n "$container_password_file" ]] && pct status "$ctid" >/dev/null 2>&1; then
    pct exec "$ctid" -- rm -f -- "$container_password_file" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

report_error() {
  local exit_code=$?
  local line_number="$1"
  local command_text="$2"
  echo "Fehler in proxmox-install-lxc.sh, Zeile $line_number: $command_text (Status $exit_code)" >&2
  exit "$exit_code"
}
trap 'report_error "$LINENO" "$BASH_COMMAND"' ERR

fail() {
  echo "Fehler: $*" >&2
  exit 1
}

if [[ -z "$bundle_path" && -z "$bundle_url" && -f "$script_dir/dpl-acron-debian13.tar.gz" ]]; then
  bundle_path="$script_dir/dpl-acron-debian13.tar.gz"
fi

[[ "$ctid" =~ ^[1-9][0-9]{2,8}$ ]] || fail "CTID muss gesetzt und eine gültige Proxmox-ID sein."
[[ -n "$web_cidrs" ]] || fail "WEB_CIDRS muss die erlaubten LAN-/VPN-Netze enthalten."
if [[ -z "$admin_password" ]]; then
  read -r -s -p "Initiales oder neues DPL2ACRON-Administratorkennwort: " admin_password </dev/tty
  echo
  read -r -s -p "Kennwort wiederholen: " password_confirmation </dev/tty
  echo
  [[ "$admin_password" == "$password_confirmation" ]] || fail "Die Kennwörter stimmen nicht überein."
  unset password_confirmation
fi
if [[ -n "$bundle_path" && -n "$bundle_url" ]]; then
  fail "BUNDLE_PATH und BUNDLE_URL dürfen nicht gleichzeitig gesetzt sein."
fi
[[ "$ct_hostname" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] || fail "Ungültiger CT_HOSTNAME."
[[ "$ip_config" == "dhcp" || "$ip_config" =~ ^[0-9.]+/[0-9]{1,2}$ ]] || fail "IP_CONFIG muss dhcp oder IPv4/CIDR sein."
if [[ "$ip_config" != "dhcp" && -z "$gateway" ]]; then
  fail "Für eine statische IP muss GATEWAY gesetzt sein."
fi
ct_exists=0
if pct status "$ctid" >/dev/null 2>&1; then
  [[ "$resume_existing" == "1" ]] || fail "CT $ctid existiert bereits. Zum Fortsetzen RESUME_EXISTING=1 setzen."
  existing_config="$(pct config "$ctid")"
  grep -Fqx "ostype: debian" <<< "$existing_config" || fail "CT $ctid ist kein Debian-Container."
  grep -Fqx "unprivileged: 1" <<< "$existing_config" || fail "CT $ctid ist nicht unprivilegiert."
  grep -Eq '^tags: .*dpl-acron' <<< "$existing_config" || fail "CT $ctid wurde nicht als DPL2ACRON-Container erkannt."
  existing_status="$(pct status "$ctid" | awk '{print $2}')"
  [[ "$existing_status" == "stopped" ]] || fail "CT $ctid muss zum sicheren Fortsetzen gestoppt sein."
  ct_exists=1
fi

validate_cidrs() {
  local value="$1"
  python3 - "$value" <<'PY' || fail "Ungültiges oder zu breites Netz: $value"
import ipaddress
import sys
for raw in sys.argv[1].split(","):
    network = ipaddress.ip_network(raw.strip(), strict=False)
    if network.prefixlen == 0:
        raise SystemExit(1)
PY
}
validate_cidrs "$web_cidrs"
if [[ -n "$ssh_cidrs" ]]; then
  validate_cidrs "$ssh_cidrs"
fi
if [[ -n "$ssh_public_keys" && ! -f "$ssh_public_keys" ]]; then
  fail "SSH_PUBLIC_KEYS wurde nicht gefunden: $ssh_public_keys"
fi

if [[ -n "$bundle_url" ]]; then
  [[ "$bundle_url" == https://* ]] || fail "BUNDLE_URL muss eine HTTPS-Adresse sein."
  [[ "$bundle_sha256" =~ ^[a-fA-F0-9]{64}$ ]] || fail "Für BUNDLE_URL ist BUNDLE_SHA256 zwingend erforderlich."
  command -v curl >/dev/null 2>&1 || fail "curl fehlt auf dem Proxmox-Host."
  bundle_file="$work_dir/dpl-acron-debian13.tar.gz"
  curl --fail --location --proto '=https' --tlsv1.2 --output "$bundle_file" "$bundle_url"
elif [[ -n "$bundle_path" ]]; then
  bundle_file="$(readlink -f -- "$bundle_path")"
  [[ -f "$bundle_file" ]] || fail "BUNDLE_PATH wurde nicht gefunden: $bundle_path"
  if [[ -z "$bundle_sha256" && -f "$bundle_file.sha256" ]]; then
    bundle_sha256="$(awk 'NR == 1 {print $1}' "$bundle_file.sha256")"
  fi
  [[ "$bundle_sha256" =~ ^[a-fA-F0-9]{64}$ ]] || fail "BUNDLE_SHA256 oder passende .sha256-Datei fehlt."
else
  fail "BUNDLE_PATH oder BUNDLE_URL muss gesetzt sein."
fi

actual_sha256="$(sha256sum "$bundle_file" | awk '{print $1}')"
[[ "${actual_sha256,,}" == "${bundle_sha256,,}" ]] || fail "SHA-256-Prüfung des Releases fehlgeschlagen."
tar -tzf "$bundle_file" >/dev/null

if [[ "$ct_exists" -eq 0 ]]; then
  echo "Debian-13-Vorlage ermitteln ..."
  pveam update
  template_name="$(pveam available --section system | awk '$2 ~ /^debian-13.*standard/ {print $2; exit}')"
  [[ -n "$template_name" ]] || fail "Keine Debian-13-Standardvorlage gefunden."
  if ! pveam list "$template_storage" | grep -Fq "/$template_name"; then
    pveam download "$template_storage" "$template_name"
  fi

  network="name=eth0,bridge=$bridge,ip=$ip_config,firewall=1"
  if [[ -n "$gateway" ]]; then
    network="$network,gw=$gateway"
  fi
  create_options=(
    "$ctid" "$template_storage:vztmpl/$template_name"
    --hostname "$ct_hostname"
    --ostype debian
    --unprivileged 1
    --cores "$cores"
    --memory "$memory"
    --swap "$swap"
    --rootfs "$root_storage:$rootfs_size"
    --net0 "$network"
    --onboot 1
    --startup "order=30,up=20,down=30"
    --timezone host
    --tags "dpl-acron;intranet"
  )
  if [[ -n "$ssh_public_keys" ]]; then
    create_options+=(--ssh-public-keys "$ssh_public_keys")
  fi

  echo "Erstelle unprivilegierten CT $ctid ..."
  pct create "${create_options[@]}"
else
  echo "Setze die abgebrochene Installation in CT $ctid fort ..."
fi

echo "Konfiguriere Proxmox-Firewall ..."
firewall_temp="$(mktemp)"
{
  echo "[OPTIONS]"
  echo "enable: 1"
  echo "policy_in: DROP"
  echo "policy_out: ACCEPT"
  echo
  echo "[RULES]"
  echo "IN ACCEPT -p icmp -log nolog"
  if [[ "$ip_config" == "dhcp" ]]; then
    echo "IN ACCEPT -p udp -sport 67 -dport 68 -log nolog"
  fi
  IFS=',' read -r -a web_list <<< "$web_cidrs"
  for cidr in "${web_list[@]}"; do
    echo "IN ACCEPT -source $cidr -p tcp -dport 443 -log nolog"
  done
  if [[ -n "$ssh_cidrs" ]]; then
    IFS=',' read -r -a ssh_list <<< "$ssh_cidrs"
    for cidr in "${ssh_list[@]}"; do
      echo "IN ACCEPT -source $cidr -p tcp -dport 22 -log nolog"
    done
  fi
} > "$firewall_temp"
# /etc/pve ist pmxcfs. Ein normales Kopieren funktioniert dort, während
# `install -m` wegen seines anschließenden chmod mit EPERM abbrechen kann.
cp -- "$firewall_temp" "/etc/pve/firewall/$ctid.fw"

# Erst nach dem Schreiben der Allow-Regeln starten, damit es kein kurzes
# ungeschütztes Zeitfenster mit eingehender Standardrichtlinie ACCEPT gibt.
pct start "$ctid"

echo "Warte auf Netzwerk und Paketquellen ..."
network_ready=0
for _ in {1..30}; do
  if pct exec "$ctid" -- getent hosts deb.debian.org >/dev/null 2>&1; then
    network_ready=1
    break
  fi
  sleep 2
done
[[ "$network_ready" -eq 1 ]] || fail "Der CT erreicht die Debian-Paketquellen nicht."

echo "Übertrage das geprüfte Release in CT $ctid ..."
pct push "$ctid" "$bundle_file" /tmp/dpl-acron-debian13.tar.gz
echo "Entpacke das Release im Container ..."
pct exec "$ctid" -- mkdir -p /root/dpl-acron-release
pct exec "$ctid" -- chmod 0700 /root/dpl-acron-release
pct exec "$ctid" -- tar -xzf /tmp/dpl-acron-debian13.tar.gz -C /root/dpl-acron-release
echo "Starte die Debian-13-Anwendungsinstallation ..."
admin_password_file="$work_dir/admin-password"
container_password_file="/root/.dpl-admin-password-$$"
umask 077
printf '%s' "$admin_password" > "$admin_password_file"
pct push "$ctid" "$admin_password_file" "$container_password_file"
pct exec "$ctid" -- chmod 0600 "$container_password_file"
pct exec "$ctid" -- env \
  "DPL_ADMIN_USERNAME=$admin_username" \
  "DPL_SECURE_COOKIE=1" \
  "DPL_TRUST_PROXY=1" \
  "DPL_UPDATE_BASE_URL=$update_base_url" \
  "DPL_UPDATE_ALLOW_HTTP=$update_allow_http" \
  "DPL_ADMIN_PASSWORD_FILE=$container_password_file" \
  bash -c 'export DPL_ADMIN_PASSWORD="$(cat "$DPL_ADMIN_PASSWORD_FILE")"; rm -f -- "$DPL_ADMIN_PASSWORD_FILE"; unset DPL_ADMIN_PASSWORD_FILE; exec bash /root/dpl-acron-release/deploy/install-debian13.sh'
unset admin_password

health="$(pct exec "$ctid" -- python3 -c \
  'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8765/api/health", timeout=5).read().decode())')"
ct_ip="$(pct exec "$ctid" -- hostname -I | awk '{print $1}')"

echo
echo "Installation erfolgreich: $health"
echo "Weboberfläche: https://${ct_ip:-CT-IP}/"
echo "Interne CA kopieren: pct pull $ctid /etc/dpl-acron/tls/ca.crt ./dpl2acron-ca.crt"
echo "Container-Konsole: pct enter $ctid"
echo "Dienststatus: pct exec $ctid -- systemctl status dpl-acron --no-pager"
echo "Firewall: /etc/pve/firewall/$ctid.fw"
