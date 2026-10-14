#!/usr/bin/env bash
set -Eeuo pipefail

backup_dir="/var/backups/dpl-acron"
client_config="/etc/dpl-acron/mariadb-client.cnf"
database_env="/etc/dpl-acron/database.env"
status_dir="/var/lib/dpl-acron-backup"
status_file="$status_dir/backup-status.json"
timestamp="$(date -u +%Y%m%d-%H%M%S)"
label="${DPL_BACKUP_LABEL:-scheduled}"
if [[ ! "$label" =~ ^[a-zA-Z0-9_-]+$ ]]; then
  echo "Ungültige Sicherungskennzeichnung: $label" >&2
  exit 1
fi
target="$backup_dir/dpl-acron-$timestamp-$label.sql.gz"
temporary="$target.partial"
state_target="$backup_dir/dpl-acron-$timestamp-$label-state.tar.gz"
state_temporary="$state_target.partial"
status_temporary=""

if [[ ! -r "$database_env" ]]; then
  echo "Datenbankkonfiguration fehlt: $database_env" >&2
  exit 1
fi
set -a
source "$database_env"
set +a
database_name="${DPL_DB_NAME:-dpl_acron}"
if [[ ! "$database_name" =~ ^[a-zA-Z0-9_]+$ ]]; then
  echo "Ungültiger Datenbankname: $database_name" >&2
  exit 1
fi

umask 077
[[ ! -L "$status_dir" ]] || { echo "Unsicheres Statusverzeichnis: $status_dir" >&2; exit 1; }
install -d -o root -g root -m 0700 "$backup_dir"
install -d -o root -g dpl-acron -m 0750 "$status_dir"
status_temporary="$(mktemp -p "$status_dir" .backup-status.XXXXXX)"
trap 'rm -f -- "$temporary" "$state_temporary" "${status_temporary:-}"' EXIT

mariadb-dump \
  --defaults-extra-file="$client_config" \
  --single-transaction \
  --quick \
  --skip-lock-tables \
  --no-tablespaces \
  --default-character-set=utf8mb4 \
  "$database_name" \
  | gzip -9 > "$temporary"

gzip -t "$temporary"
mv -- "$temporary" "$target"

# Die SQL-Sicherung allein reicht für einen Vollrestore nicht.  Die zweite,
# root-only Datei enthält Uploadquellen, Module und Dienstkonfiguration.
tar --create --gzip --file "$state_temporary" --ignore-failed-read \
  /var/lib/dpl-acron/data /etc/dpl-acron /etc/default/dpl-acron \
  /etc/systemd/system/dpl-acron.service /etc/systemd/system/dpl-acron-backup.service \
  /etc/systemd/system/dpl-acron-backup.timer
gzip -t "$state_temporary"
mv -- "$state_temporary" "$state_target"

printf '{"filename":"%s","stateFilename":"%s","createdAt":"%s"}\n' \
  "$(basename "$target")" "$(basename "$state_target")" \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$status_temporary"
chown root:dpl-acron "$status_temporary"
chmod 0640 "$status_temporary"
mv -T -- "$status_temporary" "$status_file"
status_temporary=""
find "$backup_dir" -maxdepth 1 -type f -name 'dpl-acron-*.sql.gz' -mtime +14 -delete
find "$backup_dir" -maxdepth 1 -type f -name 'dpl-acron-*-state.tar.gz' -mtime +14 -delete
echo "MariaDB-Sicherung erstellt: $target"
echo "Zustandssicherung erstellt: $state_target"
