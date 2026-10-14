#!/usr/bin/env bash
set -Eeuo pipefail

admin_password_input="${DPL_ADMIN_PASSWORD:-}"
unset DPL_ADMIN_PASSWORD

if [[ "${EUID}" -ne 0 ]]; then
  echo "Bitte mit sudo ausführen: sudo bash deploy/install-debian13.sh" >&2
  exit 1
fi

source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
install_dir="/opt/dpl-acron"
state_dir="/var/lib/dpl-acron"
config_dir="/etc/dpl-acron"
database_env="$config_dir/database.env"
auth_env="$config_dir/auth.env"
client_config="$config_dir/mariadb-client.cnf"
backup_dir="/var/backups/dpl-acron"
backup_status_dir="/var/lib/dpl-acron-backup"
tls_dir="$config_dir/tls"
service_user="dpl-acron"
service_group="dpl-acron"
service_was_active=0
migration_privileges_granted=0
rollback_dir=""
database_backup=""
update_base_url="${DPL_UPDATE_BASE_URL:-}"
update_allow_http="${DPL_UPDATE_ALLOW_HTTP:-0}"

if [[ ! -f "$auth_env" && -z "$admin_password_input" ]]; then
  if [[ -r /dev/tty ]]; then
    read -r -s -p "Initiales DPL2ACRON-Administratorkennwort: " admin_password_input </dev/tty
    echo
    read -r -s -p "Kennwort wiederholen: " password_confirmation </dev/tty
    echo
    [[ "$admin_password_input" == "$password_confirmation" ]] || {
      echo "Die Kennwörter stimmen nicht überein." >&2
      exit 1
    }
    unset password_confirmation
  else
    echo "Erstinstallation abgebrochen: DPL_ADMIN_PASSWORD muss sicher übergeben werden." >&2
    exit 1
  fi
fi

restore_service_after_failure() {
  local exit_code=$?
  if [[ "$exit_code" -ne 0 ]]; then
    # Bei einem fehlgeschlagenen Upgrade gilt fail closed: Weder eine neue noch
    # eine zurückgerollte, eventuell anders konfigurierte Version wird automatisch
    # über den bereits geänderten Proxy veröffentlicht.
    systemctl stop dpl-acron.service nginx.service >/dev/null 2>&1 || true
  fi
  if [[ "$migration_privileges_granted" -eq 1 && -n "${DPL_DB_NAME:-}" && -n "${DPL_DB_USER:-}" ]]; then
    mariadb --protocol=socket -e \
      "REVOKE CREATE, ALTER, INDEX, DROP, REFERENCES, CREATE VIEW ON \`$DPL_DB_NAME\`.* FROM '$DPL_DB_USER'@'localhost'" || true
  fi
  if [[ "$exit_code" -ne 0 && -n "$database_backup" && -r "$database_backup" ]]; then
    echo "Installation fehlgeschlagen; Datenbank wird auf den Vorzustand zurückgesetzt." >&2
    gzip -dc -- "$database_backup" | mariadb --protocol=socket "${DPL_DB_NAME:-dpl_acron}" || true
  fi
  if [[ "$exit_code" -ne 0 && -n "$rollback_dir" && -d "$rollback_dir" ]]; then
    echo "Installation fehlgeschlagen; Programmdateien werden auf den Vorzustand zurückgesetzt." >&2
    find "$install_dir" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + || true
    cp -a -- "$rollback_dir/." "$install_dir/" || true
  fi
  if [[ "$exit_code" -ne 0 && "$service_was_active" -eq 1 ]]; then
    echo "Installation fehlgeschlagen; der Dienst bleibt aus Sicherheitsgründen gestoppt." >&2
    echo "Nach Fehlerbehebung Installer erneut ausführen und erst dann den Dienst starten." >&2
  fi
  exit "$exit_code"
}
trap restore_service_after_failure EXIT

if [[ -r /etc/os-release ]]; then
  source /etc/os-release
  if [[ "${ID:-}" != "debian" || "${VERSION_ID:-}" != "13" ]]; then
    echo "Hinweis: Das Skript ist für Debian 13 vorgesehen; erkannt wurde ${PRETTY_NAME:-ein anderes System}."
  fi
fi

packages_missing=0
command -v python3 >/dev/null 2>&1 || packages_missing=1
command -v mariadbd >/dev/null 2>&1 || packages_missing=1
command -v mariadb >/dev/null 2>&1 || packages_missing=1
command -v mariadb-dump >/dev/null 2>&1 || packages_missing=1
command -v runuser >/dev/null 2>&1 || packages_missing=1
command -v nginx >/dev/null 2>&1 || packages_missing=1
command -v openssl >/dev/null 2>&1 || packages_missing=1
python3 -c 'import mariadb' >/dev/null 2>&1 || packages_missing=1
if [[ "$packages_missing" -eq 1 ]]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y --no-install-recommends python3 python3-mariadb-connector mariadb-server mariadb-client util-linux ca-certificates gzip nginx openssl
fi

python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else "Python 3.11 oder neuer ist erforderlich.")'
python3 -c 'import mariadb; print("MariaDB Connector/Python:", mariadb.__version__)'

systemctl enable --now mariadb.service
systemctl is-active --quiet mariadb.service || {
  echo "MariaDB konnte nicht gestartet werden." >&2
  exit 1
}

required_files=(
  "app.py"
  "pyproject.toml"
  "README.md"
  "DEPLOY_DEBIAN13.md"
  "PROXMOX_CT.md"
  "PROXMOX_TEST.md"
  "brauckgraben_neu.csv.xlsx"
  "shhamm_provider1_WINCC60.csv"
  "2026-06-12 DPL_PWK Lippestraße; Braukgraben.xlsx"
  "deploy/dpl-acron.service"
  "deploy/dpl-acron.env"
  "deploy/dpl-acron-backup.service"
  "deploy/dpl-acron-backup.timer"
  "deploy/backup-mariadb.sh"
  "deploy/nginx-dpl-acron.conf.example"
  "deploy/proxmox-install-lxc.sh"
)
for relative_path in "${required_files[@]}"; do
  if [[ ! -f "$source_dir/$relative_path" ]]; then
    echo "Erforderliche Datei fehlt: $relative_path" >&2
    exit 1
  fi
done
if [[ ! -d "$source_dir/dpl_export" ]]; then
  echo "Python-Paket fehlt: dpl_export" >&2
  exit 1
fi

echo "Prüfe Release vor der Installation ..."
PYTHONDONTWRITEBYTECODE=1 python3 "$source_dir/app.py" --version
PYTHONDONTWRITEBYTECODE=1 python3 - "$source_dir" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from dpl_export.modules import list_modules

modules = list_modules()
if [module.code for module in modules] != ["pwk", "rue", "rueb"]:
    raise SystemExit("Die internen Anlagenmodule sind unvollständig.")
for module in modules:
    json.loads(module.snapshot())
print("Anlagenmodule geprüft:", ", ".join(module.code.upper() for module in modules))
PY

if ! getent group "$service_group" >/dev/null; then
  groupadd --system "$service_group"
fi
if ! id "$service_user" >/dev/null 2>&1; then
  useradd --system --gid "$service_group" --home-dir "$state_dir" --create-home --shell /usr/sbin/nologin "$service_user"
fi

if systemctl is-active --quiet dpl-acron.service; then
  service_was_active=1
fi
if systemctl list-unit-files dpl-acron.service >/dev/null 2>&1; then
  systemctl stop dpl-acron.service || true
fi

if [[ -f "$install_dir/app.py" ]]; then
  rollback_dir="$(mktemp -d /opt/dpl-acron-rollback.XXXXXX)"
  chmod 0700 "$rollback_dir"
  cp -a -- "$install_dir/." "$rollback_dir/"
fi

install -d -o root -g root -m 0755 "$install_dir"
install -d -o "$service_user" -g "$service_group" -m 0750 "$state_dir" "$state_dir/data"
install -d -o root -g "$service_group" -m 0750 "$config_dir"
[[ ! -L "$backup_status_dir" ]] || { echo "Unsicheres Statusverzeichnis: $backup_status_dir" >&2; exit 1; }
install -d -o root -g "$service_group" -m 0750 "$backup_status_dir"
install -d -o root -g root -m 0700 "$backup_dir"
install -d -o root -g root -m 0700 "$tls_dir"

rm -rf -- "$install_dir/dpl_export" "$install_dir/deploy"
install -d -o root -g root -m 0755 "$install_dir/dpl_export" "$install_dir/deploy"
cp -a -- "$source_dir/dpl_export/." "$install_dir/dpl_export/"
find "$install_dir/dpl_export" -type d -exec chmod 0755 {} +
find "$install_dir/dpl_export" -type f -exec chmod 0644 {} +
chown -R root:root "$install_dir/dpl_export"
find "$install_dir/dpl_export" -type d -name '__pycache__' -prune -exec rm -rf -- {} +

cp -a -- "$source_dir/deploy/." "$install_dir/deploy/"
find "$install_dir/deploy" -type d -exec chmod 0755 {} +
find "$install_dir/deploy" -type f -exec chmod 0644 {} +
find "$install_dir/deploy" -type f -name '*.sh' -exec chmod 0755 {} +
chown -R root:root "$install_dir/deploy"

# Tests gehören nicht in die Betriebsinstallation. Insbesondere dürfen alte
# Teststände eines früheren Releases keinen Upgradeabbruch verursachen.
rm -rf -- "$install_dir/tests"

install -o root -g root -m 0644 "$source_dir/app.py" "$install_dir/app.py"
install -o root -g root -m 0644 "$source_dir/pyproject.toml" "$install_dir/pyproject.toml"
install -o root -g root -m 0644 "$source_dir/README.md" "$install_dir/README.md"
install -o root -g root -m 0644 "$source_dir/DEPLOY_DEBIAN13.md" "$install_dir/DEPLOY_DEBIAN13.md"
install -o root -g root -m 0644 "$source_dir/PROXMOX_CT.md" "$install_dir/PROXMOX_CT.md"
install -o root -g root -m 0644 "$source_dir/PROXMOX_TEST.md" "$install_dir/PROXMOX_TEST.md"
install -o root -g root -m 0644 "$source_dir/brauckgraben_neu.csv.xlsx" "$install_dir/brauckgraben_neu.csv.xlsx"
install -o root -g root -m 0644 "$source_dir/shhamm_provider1_WINCC60.csv" "$install_dir/shhamm_provider1_WINCC60.csv"
install -o root -g root -m 0644 "$source_dir/2026-06-12 DPL_PWK Lippestraße; Braukgraben.xlsx" "$install_dir/2026-06-12 DPL_PWK Lippestraße; Braukgraben.xlsx"
rm -f -- /usr/local/sbin/dpl-acron-user

if [[ ! -f "$auth_env" || -n "$admin_password_input" ]]; then
  admin_username="${DPL_ADMIN_USERNAME:-admin}"
  [[ "$admin_username" =~ ^[a-zA-Z0-9._-]{1,64}$ ]] || {
    echo "DPL_ADMIN_USERNAME enthält ungültige Zeichen." >&2
    exit 1
  }
  admin_password="$admin_password_input"
  password_hash="$(
    printf '%s' "$admin_password" |
      PYTHONPATH="$install_dir" python3 -c \
        'import sys; from dpl_export.auth import hash_password; print(hash_password(sys.stdin.read()))'
  )"
  install -o root -g "$service_group" -m 0640 /dev/null "$auth_env"
  printf '%s\n' \
    "DPL_AUTH_USERNAME=$admin_username" \
    "DPL_AUTH_PASSWORD_HASH='$password_hash'" \
    'DPL_SECURE_COOKIE=1' \
    'DPL_TRUST_PROXY=1' > "$auth_env"
  unset admin_password password_hash admin_password_input
fi

if [[ -n "$update_base_url" ]]; then
  [[ "$update_base_url" =~ ^https?://[^[:space:]]+$ ]] || {
    echo "DPL_UPDATE_BASE_URL ist ungültig." >&2
    exit 1
  }
  [[ "$update_allow_http" == "0" || "$update_allow_http" == "1" ]] || {
    echo "DPL_UPDATE_ALLOW_HTTP muss 0 oder 1 sein." >&2
    exit 1
  }
  if [[ "$update_base_url" == http://* && "$update_allow_http" != "1" ]]; then
    echo "Ein HTTP-Repository erfordert DPL_UPDATE_ALLOW_HTTP=1." >&2
    exit 1
  fi
  install -o root -g root -m 0600 /dev/null "$config_dir/update.env"
  printf '%s\n' \
    "DPL_BASE_URL=$update_base_url" \
    "DPL_ALLOW_HTTP=$update_allow_http" > "$config_dir/update.env"
fi

# Auch bestehende Installationen werden auf den verpflichtenden lokalen
# TLS-Proxy migriert; alte unsichere Cookie-/Proxywerte dürfen nicht fortleben.
sed -i -E 's/^DPL_SECURE_COOKIE=.*/DPL_SECURE_COOKIE=1/; s/^DPL_TRUST_PROXY=.*/DPL_TRUST_PROXY=1/' "$auth_env"
grep -q '^DPL_SECURE_COOKIE=' "$auth_env" || printf '%s\n' 'DPL_SECURE_COOKIE=1' >> "$auth_env"
grep -q '^DPL_TRUST_PROXY=' "$auth_env" || printf '%s\n' 'DPL_TRUST_PROXY=1' >> "$auth_env"

if [[ ! -s "$tls_dir/ca.crt" || ! -s "$tls_dir/server.crt" || ! -s "$tls_dir/server.key" ]]; then
  internal_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  internal_name="$(hostname -f 2>/dev/null || hostname)"
  openssl genrsa -out "$tls_dir/ca.key" 3072
  openssl req -x509 -new -sha256 -key "$tls_dir/ca.key" -days 3650 \
    -subj '/CN=DPL2ACRON Internal CA' -out "$tls_dir/ca.crt"
  openssl req -new -newkey rsa:3072 -nodes -keyout "$tls_dir/server.key" \
    -subj "/CN=$internal_name" -out "$tls_dir/server.csr"
  printf '%s\n' \
    'basicConstraints=CA:FALSE' \
    'keyUsage=digitalSignature,keyEncipherment' \
    'extendedKeyUsage=serverAuth' \
    "subjectAltName=DNS:$internal_name,DNS:$(hostname),IP:${internal_ip:-127.0.0.1}" > "$tls_dir/server.ext"
  openssl x509 -req -sha256 -in "$tls_dir/server.csr" -CA "$tls_dir/ca.crt" \
    -CAkey "$tls_dir/ca.key" -CAcreateserial -days 825 -extfile "$tls_dir/server.ext" \
    -out "$tls_dir/server.crt"
  rm -f -- "$tls_dir/server.csr" "$tls_dir/server.ext" "$tls_dir/ca.srl"
fi
chmod 0600 "$tls_dir/ca.key" "$tls_dir/server.key"
chmod 0644 "$tls_dir/ca.crt" "$tls_dir/server.crt"

cat > /etc/nginx/sites-available/dpl-acron.conf <<'NGINX'
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name _;
    ssl_certificate /etc/dpl-acron/tls/server.crt;
    ssl_certificate_key /etc/dpl-acron/tls/server.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_tickets off;
    client_max_body_size 25m;
    add_header Strict-Transport-Security "max-age=31536000" always;
    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_connect_timeout 10s;
        proxy_read_timeout 120s;
    }
}
NGINX
ln -sfn /etc/nginx/sites-available/dpl-acron.conf /etc/nginx/sites-enabled/dpl-acron.conf
rm -f -- /etc/nginx/sites-enabled/default
nginx -t
systemctl enable nginx.service
systemctl restart nginx.service
systemctl is-active --quiet nginx.service || {
  echo "Nginx konnte mit der HTTPS-Konfiguration nicht gestartet werden." >&2
  exit 1
}

if [[ ! -f "$database_env" ]]; then
  db_password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  db_socket="$(mariadb --batch --skip-column-names -e 'SELECT @@socket')"
  mariadb --protocol=socket <<SQL
CREATE DATABASE IF NOT EXISTS dpl_acron CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'dpl_acron'@'localhost' IDENTIFIED BY '${db_password}';
ALTER USER 'dpl_acron'@'localhost' IDENTIFIED BY '${db_password}';
GRANT SELECT, INSERT, UPDATE, DELETE, SHOW VIEW, TRIGGER
  ON dpl_acron.* TO 'dpl_acron'@'localhost';
FLUSH PRIVILEGES;
SQL
  install -o root -g "$service_group" -m 0640 /dev/null "$database_env"
  printf '%s\n' \
    'DPL_DB_DRIVER=mariadb' \
    'DPL_DB_HOST=localhost' \
    'DPL_DB_PORT=3306' \
    'DPL_DB_NAME=dpl_acron' \
    'DPL_DB_USER=dpl_acron' \
    "DPL_DB_PASSWORD=$db_password" \
    "DPL_DB_UNIX_SOCKET=$db_socket" > "$database_env"
fi

set -a
source "$database_env"
set +a

if [[ "${DPL_DB_DRIVER:-}" != "mariadb" || -z "${DPL_DB_PASSWORD:-}" ]]; then
  echo "Ungültige MariaDB-Konfiguration in $database_env" >&2
  exit 1
fi
[[ "$DPL_DB_NAME" =~ ^[a-zA-Z0-9_]+$ ]] || { echo "Ungültiger Datenbankname." >&2; exit 1; }
[[ "$DPL_DB_USER" =~ ^[a-zA-Z0-9_]+$ ]] || { echo "Ungültiger Datenbankbenutzer." >&2; exit 1; }

install -o root -g root -m 0600 /dev/null "$client_config"
printf '%s\n' \
  '[client]' \
  "user=$DPL_DB_USER" \
  "password=$DPL_DB_PASSWORD" \
  "socket=${DPL_DB_UNIX_SOCKET:-/run/mysqld/mysqld.sock}" \
  'default-character-set=utf8mb4' > "$client_config"

chown -R "$service_user:$service_group" "$state_dir"
chmod 0750 "$state_dir" "$state_dir/data"

DPL_BACKUP_LABEL=pre-upgrade "$install_dir/deploy/backup-mariadb.sh"
database_backup="$(find "$backup_dir" -maxdepth 1 -type f -name 'dpl-acron-*-pre-upgrade.sql.gz' -printf '%T@ %p\n' | sort -nr | awk 'NR == 1 {sub(/^[^ ]+ /, ""); print; exit}')"
[[ -n "$database_backup" && -r "$database_backup" ]] || { echo "Pre-Upgrade-Sicherung wurde nicht gefunden." >&2; exit 1; }
# Schemaänderungsrechte existieren nur während des gestoppten Upgrades.
mariadb --protocol=socket -e \
  "GRANT CREATE, ALTER, INDEX, DROP, REFERENCES, CREATE VIEW ON \`$DPL_DB_NAME\`.* TO '$DPL_DB_USER'@'localhost'"
migration_privileges_granted=1
runuser --preserve-environment -u "$service_user" -- python3 "$install_dir/app.py" init
runuser --preserve-environment -u "$service_user" -- python3 "$install_dir/app.py" validate >/dev/null
mariadb --protocol=socket -e \
  "REVOKE CREATE, ALTER, INDEX, DROP, REFERENCES, CREATE VIEW ON \`$DPL_DB_NAME\`.* FROM '$DPL_DB_USER'@'localhost'"
migration_privileges_granted=0

install -o root -g root -m 0644 "$source_dir/deploy/dpl-acron.service" /etc/systemd/system/dpl-acron.service
install -o root -g root -m 0644 "$source_dir/deploy/dpl-acron-backup.service" /etc/systemd/system/dpl-acron-backup.service
install -o root -g root -m 0644 "$source_dir/deploy/dpl-acron-backup.timer" /etc/systemd/system/dpl-acron-backup.timer
if [[ -f /etc/default/dpl-acron && ! -f /etc/default/dpl-acron.pre-intranet-v3 ]]; then
  cp -a -- /etc/default/dpl-acron /etc/default/dpl-acron.pre-intranet-v3
fi
install -o root -g root -m 0644 "$source_dir/deploy/dpl-acron.env" /etc/default/dpl-acron

"$install_dir/deploy/backup-mariadb.sh"
systemctl daemon-reload
systemctl enable --now dpl-acron.service dpl-acron-backup.timer nginx.service

DPL_HOST=127.0.0.1
DPL_PORT=8765
source /etc/default/dpl-acron
health_url="http://127.0.0.1:$DPL_PORT/api/health"

healthy=0
for _ in {1..15}; do
  if python3 -c 'import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=2).read()' "$health_url" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 1
done

if [[ "$healthy" -ne 1 ]]; then
  echo "Der Dienst ist nicht erreichbar." >&2
  systemctl status dpl-acron.service --no-pager || true
  exit 1
fi

echo
echo "Installation erfolgreich."
internal_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "Webdienst im Intranet/VPN: https://${internal_ip:-CT-IP}/"
echo "Interne CA für die Client-Vertrauensstellung: $tls_dir/ca.crt"
echo "MariaDB-Datenbank: $DPL_DB_NAME über ${DPL_DB_UNIX_SOCKET:-localhost}"
echo "Datenbankzugang: $database_env"
echo "Anmeldung: Benutzer ${DPL_ADMIN_USERNAME:-admin}; Konfiguration unter $auth_env"
echo "Sicherungen: $backup_dir"
echo
echo "Der Login ersetzt nicht die Zugriffsbeschränkung auf Intranet und VPN."
echo "Keine öffentliche Portweiterleitung für 8765 oder 3306 einrichten."
echo "Status: sudo systemctl status dpl-acron"
echo "Protokoll: sudo journalctl -u dpl-acron -f"
if [[ -n "$rollback_dir" ]]; then
  rm -rf -- "$rollback_dir"
  rollback_dir=""
fi
