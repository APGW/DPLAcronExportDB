#!/bin/bash -p
set -Eeuo pipefail

# Nicht-destruktive Abnahme einer installierten DPL2ACRON-Testinstanz.
# Ausführung auf dem Proxmox-VE-Host:
#   CTID=100 EXPECTED_VERSION=3.8.4 EXPECTED_VLAN_TAG=100 /bin/bash --noprofile --norc -p proxmox-acceptance.sh

ctid="${CTID:-}"
expected_version="${EXPECTED_VERSION:-3.8.4}"
expected_vlan_tag="${EXPECTED_VLAN_TAG:-}"
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

[[ "${EUID:-$(id -u)}" -eq 0 ]] || fail "Als root auf dem Proxmox-Host ausführen."
command -v pct >/dev/null 2>&1 || fail "pct wurde nicht gefunden."
command -v python3 >/dev/null 2>&1 || fail "python3 wurde nicht gefunden."
command -v curl >/dev/null 2>&1 || fail "curl wurde nicht gefunden."
[[ "$ctid" =~ ^[1-9][0-9]{2,8}$ ]] || fail "CTID muss eine gültige Proxmox-ID sein."
[[ "$expected_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "EXPECTED_VERSION ist ungültig."
if [[ -n "$expected_vlan_tag" ]]; then
  [[ "$expected_vlan_tag" =~ ^[0-9]+$ ]] && (( expected_vlan_tag >= 1 && expected_vlan_tag <= 4094 )) \
    || fail "EXPECTED_VLAN_TAG ist ungültig."
fi

config="$(pct config "$ctid")" || fail "CT $ctid wurde nicht gefunden."
grep -Fqx 'unprivileged: 1' <<< "$config" || fail "CT $ctid ist nicht unprivilegiert."
grep -Eq '^net0: .*firewall=1' <<< "$config" || fail "Die CT-Netzwerkschnittstelle verwendet nicht die Proxmox-Firewall."
if [[ -n "$expected_vlan_tag" ]]; then
  grep -Eq "^net0: .*tag=$expected_vlan_tag([,[:space:]]|$)" <<< "$config" \
    || fail "Die CT-Netzwerkschnittstelle verwendet nicht VLAN $expected_vlan_tag."
fi
pass "Unprivilegierter CT; Proxmox-CT-Regeln als zusätzliche Schutzschicht vorhanden"

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
if grep -Eq '(^|[[:space:]])(0\.0\.0\.0|\*|\[::\]|:::):22([[:space:]]|$)' <<< "$listeners"; then
  fail "SSH lauscht extern, obwohl die Datacenter-Firewall nicht vorausgesetzt wird."
fi
pass "Nur HTTPS extern; Backend und MariaDB nicht extern gebunden"

pct exec "$ctid" -- grep -Fqx '    include /etc/nginx/snippets/dpl-acron-access.conf;' \
  /etc/nginx/sites-available/dpl-acron.conf \
  || fail "Nginx bindet die verpflichtende CIDR-Zugriffsliste nicht ein."
pct exec "$ctid" -- grep -Fqx 'deny all;' /etc/nginx/snippets/dpl-acron-access.conf \
  || fail "Nginx verweigert Zugriffe außerhalb der Freigabeliste nicht."
web_cidrs="$(pct exec "$ctid" -- cat /etc/dpl-acron/web-cidrs)"
IFS=',' read -r -a web_cidr_list <<< "$web_cidrs"
for cidr in "${web_cidr_list[@]}"; do
  pct exec "$ctid" -- grep -Fqx "allow $cidr;" /etc/nginx/snippets/dpl-acron-access.conf \
    || fail "Nginx-Freigabe fehlt für $cidr."
done
pass "Nginx beschränkt HTTPS auf die konfigurierten Intranet-/VPN-Netze"

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
