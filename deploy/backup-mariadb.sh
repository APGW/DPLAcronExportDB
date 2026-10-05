#!/usr/bin/env bash
set -Eeuo pipefail

backup_dir="/var/backups/dpl-acron"
client_config="/etc/dpl-acron/mariadb-client.cnf"
database_env="/etc/dpl-acron/database.env"
status_file="/var/lib/dpl-acron/data/backup-status.json"
timestamp="$(date -u +%Y%m%d-%H%M%S)"
label="${DPL_BACKUP_LABEL:-scheduled}"
if [[ ! "$label" =~ ^[a-zA-Z0-9_-]+$ ]]; then
  echo "Ungültige Sicherungskennzeichnung: $label" >&2
  exit 1
fi
target="$backup_dir/dpl-acron-$timestamp-$label.sql.gz"
temporary="$target.partial"
status_temporary="$status_file.tmp"

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
install -d -o root -g root -m 0700 "$backup_dir"
trap 'rm -f -- "$temporary" "$status_temporary"' EXIT

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
printf '{"filename":"%s","createdAt":"%s"}\n' \
  "$(basename "$target")" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$status_temporary"
install -o root -g dpl-acron -m 0640 "$status_temporary" "$status_file"
rm -f -- "$status_temporary"
find "$backup_dir" -maxdepth 1 -type f -name 'dpl-acron-*.sql.gz' -mtime +14 -delete
echo "MariaDB-Sicherung erstellt: $target"
