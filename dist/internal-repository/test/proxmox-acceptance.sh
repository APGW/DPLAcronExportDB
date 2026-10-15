#!/bin/bash -p
set -Eeuo pipefail

# Nicht-destruktive Abnahme einer installierten DPL2ACRON-Testinstanz.
# Ausführung auf dem Proxmox-VE-Host:
#   CTID=100 EXPECTED_VERSION=3.7.4 /bin/bash -p --noprofile --norc proxmox-acceptance.sh

ctid="${CTID:-}"
expected_version="${EXPECTED_VERSION:-3.7.4}"
checks=0
temporary_dir=""
restore_database=""
builtin unset TMPDIR BASH_ENV ENV CDPATH GLOBIGNORE PYTHONPATH PYTHONHOME PYTHONSTARTUP \
  LD_PRELOAD LD_LIBRARY_PATH CURL_HOME CURL_CA_BUNDLE SSL_CERT_FILE SSL_CERT_DIR

pass() {
  checks=$((checks + 1))
  printf 'OK  %s\n' "$1"
}

fail() {
  printf 'FEHLER  %s\n' "$1" >&2
  exit 1
}

cleanup() {
  if [[ -n "$restore_database" ]] && pct status "$ctid" >/dev/null 2>&1; then
    pct exec "$ctid" -- mariadb --protocol=socket -e "DROP DATABASE IF EXISTS \`$restore_database\`" >/dev/null 2>&1 || true
  fi
  [[ -z "$temporary_dir" ]] || rm -rf -- "$temporary_dir"
}
trap cleanup EXIT

require_effective_proxmox_firewall() {
  local cluster_options node_options node_name required_backend
  cluster_options="$(pvesh get /cluster/firewall/options --output-format json 2>/dev/null)" \
    || fail "Der Datacenter-Firewallstatus konnte nicht gelesen werden."
  python3 -I - "$cluster_options" <<'PY' \
    || fail "Die Proxmox-Firewall ist im Datacenter nicht aktiviert."
import json
import sys

value = json.loads(sys.argv[1]).get("enable")
if value not in (1, True, "1", "true", "on", "yes"):
    raise SystemExit(1)
PY
  node_name="$(hostname -s)"
  [[ "$node_name" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] \
    || fail "Der lokale Proxmox-Node konnte nicht sicher bestimmt werden."
  node_options="$(pvesh get "/nodes/$node_name/firewall/options" --output-format json 2>/dev/null)" \
    || fail "Die Firewalloptionen des Proxmox-Nodes $node_name konnten nicht gelesen werden."
  required_backend="$(python3 -I - "$node_options" <<'PY'
import json
import sys

value = json.loads(sys.argv[1]).get("nftables", 0)
enabled = value in (1, True, "1", "true", "on", "yes")
print("proxmox-firewall.service" if enabled else "pve-firewall.service")
PY
)" || fail "Die Firewalloption nftables des Proxmox-Nodes ist ungültig."
  systemctl is-active --quiet "$required_backend" 2>/dev/null \
    || fail "Das konfigurierte Proxmox-Firewall-Backend ist nicht aktiv: $required_backend"
}

[[ "${EUID:-$(id -u)}" -eq 0 ]] || fail "Als root auf dem Proxmox-Host ausführen."
command -v pct >/dev/null 2>&1 || fail "pct wurde nicht gefunden."
command -v python3 >/dev/null 2>&1 || fail "python3 wurde nicht gefunden."
command -v curl >/dev/null 2>&1 || fail "curl wurde nicht gefunden."
command -v pvesh >/dev/null 2>&1 || fail "pvesh wurde nicht gefunden."
[[ "$ctid" =~ ^[1-9][0-9]{2,8}$ ]] || fail "CTID muss eine gültige Proxmox-ID sein."
[[ "$expected_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "EXPECTED_VERSION ist ungültig."

config="$(pct config "$ctid")" || fail "CT $ctid wurde nicht gefunden."
grep -Fqx 'unprivileged: 1' <<< "$config" || fail "CT $ctid ist nicht unprivilegiert."
grep -Eq '^net0: .*firewall=1' <<< "$config" || fail "Die CT-Netzwerkschnittstelle verwendet nicht die Proxmox-Firewall."
require_effective_proxmox_firewall
pass "Unprivilegierter CT und wirksame Proxmox-Firewall"

[[ "$(pct status "$ctid" | awk '{print $2}')" == "running" ]] || fail "CT $ctid läuft nicht."
os_version="$(pct exec "$ctid" -- sh -c '. /etc/os-release; printf %s "$VERSION_ID"')"
[[ "$os_version" == "13" ]] || fail "Erwartet wurde Debian 13, erkannt wurde $os_version."
pass "Debian 13 und laufender Container"

for unit in mariadb.service dpl-acron.service nginx.service dpl-acron-backup.timer; do
  pct exec "$ctid" -- systemctl is-active --quiet "$unit" || fail "$unit ist nicht aktiv."
  pct exec "$ctid" -- systemctl is-enabled --quiet "$unit" || fail "$unit ist nicht aktiviert."
done
pass "MariaDB, Anwendung, Nginx und Sicherungstimer aktiv"

version_output="$(pct exec "$ctid" -- python3 /opt/dpl-acron/app.py --version)"
[[ "$version_output" == "app.py $expected_version" ]] \
  || fail "Installierte Version ist '$version_output', erwartet wurde $expected_version."
pass "Anwendungsversion $expected_version"

listeners="$(pct exec "$ctid" -- ss -H -ltn)"
grep -Eq '127\.0\.0\.1:8765([[:space:]]|$)' <<< "$listeners" \
  || fail "Das Python-Backend lauscht nicht auf 127.0.0.1:8765."
if grep -Eq '(^|[[:space:]])(0\.0\.0\.0|\*|\[::\]|:::):8765([[:space:]]|$)' <<< "$listeners"; then
  fail "Port 8765 ist auf einem externen Interface geöffnet."
fi
grep -Eq '(:|\])443([[:space:]]|$)' <<< "$listeners" || fail "Nginx lauscht nicht auf Port 443."
if grep -Eq '(^|[[:space:]])(0\.0\.0\.0|\*|\[::\]|:::):3306([[:space:]]|$)' <<< "$listeners"; then
  fail "MariaDB lauscht extern auf Port 3306."
fi
pass "Nur HTTPS extern; Backend und MariaDB nicht extern gebunden"

firewall_file="/etc/pve/firewall/$ctid.fw"
[[ -r "$firewall_file" ]] || fail "Proxmox-Firewalldatei fehlt: $firewall_file"
grep -Fqx 'enable: 1' "$firewall_file" || fail "CT-Firewall ist nicht aktiviert."
grep -Fqx 'policy_in: DROP' "$firewall_file" || fail "Eingangsrichtlinie ist nicht DROP."
grep -Eq 'IN ACCEPT .* -p tcp -dport 443([[:space:]]|$)' "$firewall_file" \
  || fail "Keine Freigabe für HTTPS gefunden."
if grep -Eq -- '-dport (8765|3306)([[:space:]]|$)' "$firewall_file"; then
  fail "Die Firewall gibt 8765 oder 3306 frei."
fi
pass "Proxmox-Firewall erlaubt nur den vorgesehenen Webport"

[[ -d /root && ! -L /root ]] || fail "/root ist kein sicheres temporäres Stammverzeichnis."
root_owner="$(stat -Lc '%u' /root)"
root_mode="$(stat -Lc '%a' /root)"
[[ "$root_owner" == "0" && $((8#$root_mode & 022)) -eq 0 ]] \
  || fail "/root muss root gehören und darf nicht gruppen-/weltbeschreibbar sein."
temporary_dir="$(mktemp -d -p /root .dpl2acron-acceptance.XXXXXX)"
chmod 0700 "$temporary_dir"
ca_file="$temporary_dir/dpl2acron-ca.crt"
pct pull "$ctid" /etc/dpl-acron/tls/ca.crt "$ca_file" >/dev/null
ct_ip="$(pct exec "$ctid" -- hostname -I | awk '{print $1}')"
[[ -n "$ct_ip" ]] || fail "Keine Container-IP gefunden."
health="$(curl --fail --silent --show-error --cacert "$ca_file" --connect-timeout 5 "https://$ct_ip/api/health")" \
  || fail "HTTPS-Gesundheitsprüfung fehlgeschlagen."
python3 - "$expected_version" "$health" <<'PY' || fail "Gesundheitsantwort ist ungültig."
import json
import sys

expected, raw = sys.argv[1], sys.argv[2]
payload = json.loads(raw)
if payload != {"status": "ok", "version": expected}:
    raise SystemExit(1)
PY
pass "TLS-Zertifikat und öffentlicher Gesundheitsendpunkt"

for config_file in /etc/dpl-acron/auth.env /etc/dpl-acron/database.env /etc/dpl-acron/update.env; do
  [[ "$(pct exec "$ctid" -- stat -c '%U:%G:%a' "$config_file")" == root:*:6?? ]] \
    || fail "Unsichere Eigentumsrechte für $config_file."
done
pct exec "$ctid" -- grep -Fqx 'DPL_SECURE_COOKIE=1' /etc/dpl-acron/auth.env \
  || fail "Secure-Cookie ist nicht aktiviert."
pct exec "$ctid" -- grep -Fqx 'DPL_TRUST_PROXY=1' /etc/dpl-acron/auth.env \
  || fail "Reverse-Proxy-Vertrauen ist nicht aktiviert."
pass "Root-kontrollierte Zugangsdaten und sichere Sitzungskonfiguration"

grants="$(pct exec "$ctid" -- mariadb --defaults-extra-file=/etc/dpl-acron/mariadb-client.cnf --batch --skip-column-names -e 'SHOW GRANTS FOR CURRENT_USER')"
grep -Eq 'SELECT, INSERT, UPDATE, DELETE' <<< "$grants" || fail "Erforderliche Laufzeitrechte fehlen."
if grep -Eq 'ALL PRIVILEGES|(^|[, ])(CREATE|ALTER|DROP|GRANT OPTION)([, ]|$)' <<< "$grants"; then
  fail "Der Laufzeitbenutzer besitzt unerlaubte Schema- oder Administrationsrechte."
fi
pass "MariaDB-Laufzeitbenutzer ohne dauerhafte Schemarechte"

pct exec "$ctid" -- systemctl start dpl-acron-backup.service \
  || fail "Testsicherung konnte nicht gestartet werden."
[[ "$(pct exec "$ctid" -- systemctl show dpl-acron-backup.service -p Result --value)" == "success" ]] \
  || fail "Testsicherung war nicht erfolgreich."
pct exec "$ctid" -- test ! -L /var/lib/dpl-acron-backup/backup-status.json \
  || fail "Backupstatus ist ein symbolischer Link."
pct exec "$ctid" -- python3 -c '
import json
from pathlib import Path
status = json.loads(Path("/var/lib/dpl-acron-backup/backup-status.json").read_text())
for key in ("filename", "stateFilename", "createdAt"):
    if not status.get(key):
        raise SystemExit(1)
for key in ("filename", "stateFilename"):
    path = Path("/var/backups/dpl-acron") / status[key]
    if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
        raise SystemExit(1)
' || fail "Sicherungsdateien oder Status sind ungültig."
pass "Datenbank- und Zustandssicherung mit sicherem Status"

latest_sql="$(pct exec "$ctid" -- sh -c "find /var/backups/dpl-acron -maxdepth 1 -type f -name 'dpl-acron-*-scheduled.sql.gz' -printf '%T@ %p\\n' | sort -nr | awk 'NR == 1 {sub(/^[^ ]+ /, \"\"); print}'")"
latest_state="${latest_sql%.sql.gz}-state.tar.gz"
[[ -n "$latest_sql" ]] || fail "Keine geplante SQL-Sicherung gefunden."
pct exec "$ctid" -- gzip -t "$latest_sql" || fail "SQL-Sicherung ist beschädigt."
pct exec "$ctid" -- tar -tzf "$latest_state" >/dev/null || fail "Zustandssicherung ist beschädigt."

restore_database="dpl_acron_restore_test_$$"
pct exec "$ctid" -- mariadb --protocol=socket -e "CREATE DATABASE \`$restore_database\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
pct exec "$ctid" -- sh -c "gzip -dc -- '$latest_sql' | mariadb --protocol=socket '$restore_database'" \
  || fail "Testwiederherstellung der SQL-Sicherung fehlgeschlagen."
restored_tables="$(pct exec "$ctid" -- mariadb --protocol=socket --batch --skip-column-names -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$restore_database'")"
[[ "$restored_tables" -gt 0 ]] || fail "Testwiederherstellung enthält keine Tabellen."
pct exec "$ctid" -- mariadb --protocol=socket -e "DROP DATABASE \`$restore_database\`"
restore_database=""
pass "Integritätsprüfung und Wiederherstellung in temporäre Datenbank"

printf '\nABNAHME ERFOLGREICH: %s Prüfgruppen bestanden.\n' "$checks"
printf 'DPL2ACRON %s: https://%s/\n' "$expected_version" "$ct_ip"
