#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Bitte mit sudo ausführen: sudo bash deploy/install-debian13.sh" >&2
  exit 1
fi

source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
install_dir="/opt/dpl-acron"
state_dir="/var/lib/dpl-acron"
config_dir="/etc/dpl-acron"
database_env="$config_dir/database.env"
client_config="$config_dir/mariadb-client.cnf"
backup_dir="/var/backups/dpl-acron"
service_user="dpl-acron"
service_group="dpl-acron"
service_was_active=0

restore_service_after_failure() {
  local exit_code=$?
  if [[ "$exit_code" -ne 0 && "$service_was_active" -eq 1 ]]; then
    echo "Installation fehlgeschlagen; zuvor laufender Dienst wird wieder gestartet." >&2
    systemctl daemon-reload || true
    systemctl start dpl-acron.service || true
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
python3 -c 'import mariadb' >/dev/null 2>&1 || packages_missing=1
if [[ "$packages_missing" -eq 1 ]]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y --no-install-recommends python3 python3-mariadb-connector mariadb-server mariadb-client util-linux ca-certificates gzip
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

install -d -o root -g root -m 0755 "$install_dir"
install -d -o "$service_user" -g "$service_group" -m 0750 "$state_dir" "$state_dir/data"
install -d -o root -g "$service_group" -m 0750 "$config_dir"
install -d -o root -g root -m 0700 "$backup_dir"

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
install -o root -g root -m 0644 "$source_dir/brauckgraben_neu.csv.xlsx" "$install_dir/brauckgraben_neu.csv.xlsx"
install -o root -g root -m 0644 "$source_dir/shhamm_provider1_WINCC60.csv" "$install_dir/shhamm_provider1_WINCC60.csv"
install -o root -g root -m 0644 "$source_dir/2026-06-12 DPL_PWK Lippestraße; Braukgraben.xlsx" "$install_dir/2026-06-12 DPL_PWK Lippestraße; Braukgraben.xlsx"
rm -f -- /usr/local/sbin/dpl-acron-user

if [[ ! -f "$database_env" ]]; then
  db_password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  db_socket="$(mariadb --batch --skip-column-names -e 'SELECT @@socket')"
  mariadb --protocol=socket <<SQL
CREATE DATABASE IF NOT EXISTS dpl_acron CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'dpl_acron'@'localhost' IDENTIFIED BY '${db_password}';
ALTER USER 'dpl_acron'@'localhost' IDENTIFIED BY '${db_password}';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP, REFERENCES, CREATE VIEW, SHOW VIEW
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
runuser --preserve-environment -u "$service_user" -- python3 "$install_dir/app.py" init
runuser --preserve-environment -u "$service_user" -- python3 "$install_dir/app.py" validate >/dev/null

install -o root -g root -m 0644 "$source_dir/deploy/dpl-acron.service" /etc/systemd/system/dpl-acron.service
install -o root -g root -m 0644 "$source_dir/deploy/dpl-acron-backup.service" /etc/systemd/system/dpl-acron-backup.service
install -o root -g root -m 0644 "$source_dir/deploy/dpl-acron-backup.timer" /etc/systemd/system/dpl-acron-backup.timer
if [[ -f /etc/default/dpl-acron && ! -f /etc/default/dpl-acron.pre-intranet-v3 ]]; then
  cp -a -- /etc/default/dpl-acron /etc/default/dpl-acron.pre-intranet-v3
fi
install -o root -g root -m 0644 "$source_dir/deploy/dpl-acron.env" /etc/default/dpl-acron

"$install_dir/deploy/backup-mariadb.sh"
systemctl daemon-reload
systemctl enable --now dpl-acron.service dpl-acron-backup.timer

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
echo "Webdienst im Intranet/VPN: http://${internal_ip:-CT-IP}:$DPL_PORT/"
echo "MariaDB-Datenbank: $DPL_DB_NAME über ${DPL_DB_UNIX_SOCKET:-localhost}"
echo "Datenbankzugang: $database_env"
echo "Sicherungen: $backup_dir"
echo
echo "Kein Login: Zugriff ausschließlich per Proxmox-/Netzwerk-Firewall auf LAN und VPN begrenzen."
echo "Keine öffentliche Portweiterleitung für 8765 oder 3306 einrichten."
echo "Status: sudo systemctl status dpl-acron"
echo "Protokoll: sudo journalctl -u dpl-acron -f"
