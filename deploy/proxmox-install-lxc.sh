#!/bin/bash -p
set -Eeuo pipefail

admin_password="${DPL_ADMIN_PASSWORD:-}"
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
allow_public_cidrs="${DPL_ALLOW_PUBLIC_CIDRS:-0}"
ca_cert="${DPL_CA_CERT:-}"

for function_name in $(builtin compgen -A function); do
  builtin unset -f "$function_name"
done
builtin unset DPL_ADMIN_PASSWORD BASH_ENV ENV CDPATH GLOBIGNORE PYTHONPATH PYTHONHOME PYTHONSTARTUP \
  LD_PRELOAD LD_LIBRARY_PATH CURL_HOME CURL_CA_BUNDLE SSL_CERT_FILE SSL_CERT_DIR
builtin export IFS=$' \t\n'
builtin export PATH=/usr/sbin:/usr/bin:/sbin:/bin

readonly BASH_BIN=/bin/bash
readonly CURL_BIN=/usr/bin/curl
readonly ENV_BIN=/usr/bin/env
readonly PYTHON_BIN=/usr/bin/python3
readonly SHA256SUM_BIN=/usr/bin/sha256sum

# Vollständige Installation eines neuen Debian-13-LXC auf einem Proxmox-VE-Host.
# Pflichtangaben:
#   /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin CTID=3072 \
#   WEB_CIDRS="192.168.133.0/24,10.20.0.0/16" \
#   /bin/bash -p --noprofile --norc proxmox-install-lxc.sh
# Alternativ zum lokalen Archiv:
#   BUNDLE_URL=https://intern.example/dpl-acron-debian13.tar.gz \
#   BUNDLE_SHA256=<64-zeichen> ... /bin/bash -p --noprofile --norc proxmox-install-lxc.sh

if [[ "${EUID}" -ne 0 ]] || [[ ! -x /usr/sbin/pct ]]; then
  echo "Dieses Skript muss als root auf einem Proxmox-VE-Host laufen." >&2
  exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
work_dir="$(mktemp -d)"
firewall_temp=""
container_password_file=""
container_ca_file=""

cleanup() {
  rm -rf -- "$work_dir"
  if [[ -n "$firewall_temp" ]]; then
    rm -f -- "$firewall_temp"
  fi
  if [[ -n "$container_password_file" ]] && pct status "$ctid" >/dev/null 2>&1; then
    pct exec "$ctid" -- rm -f -- "$container_password_file" >/dev/null 2>&1 || true
  fi
  if [[ -n "$container_ca_file" ]] && pct status "$ctid" >/dev/null 2>&1; then
    pct exec "$ctid" -- rm -f -- "$container_ca_file" >/dev/null 2>&1 || true
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
[[ "$allow_public_cidrs" == "0" || "$allow_public_cidrs" == "1" ]] \
  || fail "DPL_ALLOW_PUBLIC_CIDRS muss 0 oder 1 sein."
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

normalize_cidrs() {
  local value="$1"
  "$PYTHON_BIN" -I - "$value" "$allow_public_cidrs" <<'PY'
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
}
web_cidrs="$(normalize_cidrs "$web_cidrs")" \
  || fail "WEB_CIDRS enthält ein ungültiges, spezielles, öffentliches oder zu breites Netz."
if [[ -n "$ssh_cidrs" ]]; then
  ssh_cidrs="$(normalize_cidrs "$ssh_cidrs")" \
    || fail "SSH_CIDRS enthält ein ungültiges, spezielles, öffentliches oder zu breites Netz."
fi
if [[ -n "$ssh_public_keys" && ! -f "$ssh_public_keys" ]]; then
  fail "SSH_PUBLIC_KEYS wurde nicht gefunden: $ssh_public_keys"
fi
if [[ -n "$ca_cert" ]]; then
  [[ "$ca_cert" == /* && -f "$ca_cert" && ! -L "$ca_cert" ]] \
    || fail "DPL_CA_CERT muss eine absolute, reguläre CA-Datei auf dem Proxmox-Host sein."
  ca_mode="$(stat -c '%a' -- "$ca_cert")"
  [[ "$(stat -c '%u' -- "$ca_cert")" == "0" ]] \
    && (( (8#$ca_mode & 8#022) == 0 )) \
    || fail "DPL_CA_CERT muss root gehören und darf nicht gruppen-/weltbeschreibbar sein."
fi

if [[ -n "$bundle_url" ]]; then
  [[ "$bundle_url" == https://* ]] || fail "BUNDLE_URL muss eine HTTPS-Adresse sein."
  [[ "$bundle_sha256" =~ ^[a-fA-F0-9]{64}$ ]] || fail "Für BUNDLE_URL ist BUNDLE_SHA256 zwingend erforderlich."
  [[ -x "$CURL_BIN" ]] || fail "curl fehlt auf dem Proxmox-Host."
  bundle_file="$work_dir/dpl-acron-debian13.tar.gz"
  curl_ca_options=()
  if [[ -n "$ca_cert" ]]; then
    curl_ca_options=(--cacert "$ca_cert")
  fi
  ( ulimit -f 25600; "$CURL_BIN" --disable --fail --location --proto '=https' \
    --proto-redir '=https' --tlsv1.2 --connect-timeout 10 --max-time 300 \
    --speed-limit 1024 --speed-time 30 --max-filesize 26214400 \
    "${curl_ca_options[@]}" \
    --output "$bundle_file" "$bundle_url" )
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

actual_sha256="$("$SHA256SUM_BIN" "$bundle_file" | awk '{print $1}')"
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
if [[ -n "$ca_cert" ]]; then
  container_ca_file="/root/.dpl-repository-ca-$$.crt"
  pct push "$ctid" "$ca_cert" "$container_ca_file"
  pct exec "$ctid" -- chmod 0600 "$container_ca_file"
fi
pct exec "$ctid" -- /usr/bin/env -i HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  "DPL_ADMIN_USERNAME=$admin_username" \
  "DPL_SECURE_COOKIE=1" \
  "DPL_TRUST_PROXY=1" \
  "DPL_UPDATE_BASE_URL=$update_base_url" \
  "DPL_UPDATE_ALLOW_HTTP=$update_allow_http" \
  "DPL_UPDATE_CA_CERT_SOURCE=$container_ca_file" \
  "DPL_ADMIN_PASSWORD_FILE=$container_password_file" \
  /bin/bash -p --noprofile --norc -c \
  'set -Eeuo pipefail; export DPL_ADMIN_PASSWORD="$(/usr/bin/cat -- "$DPL_ADMIN_PASSWORD_FILE")"; /bin/rm -f -- "$DPL_ADMIN_PASSWORD_FILE"; unset DPL_ADMIN_PASSWORD_FILE; exec /bin/bash -p --noprofile --norc /root/dpl-acron-release/deploy/install-debian13.sh'
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
