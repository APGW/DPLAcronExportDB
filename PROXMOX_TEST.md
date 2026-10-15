# Proxmox-Abnahme für DPL2ACRON 3.7.4

Diese Abnahme ist für einen ausschließlich zu Testzwecken verwendeten,
unprivilegierten Debian-13-LXC vorgesehen. Vor einem Upgrade einer vorhandenen
Instanz muss ein Proxmox-Backup oder Snapshot erstellt werden.

## 1. Internes Repository bereitstellen

Der vollständige Ordner `dist/internal-repository` wird mit dem Repository auf
GitHub veröffentlicht. Die Basisadresse des unveränderlichen Release-Tags lautet:

```text
https://raw.githubusercontent.com/APGW/DPLAcronExportDB/v3.7.4/dist/internal-repository
```

SHA-256-Wert des Einstiegsskripts muss aus einer vertrauenswürdigen Quelle
übernommen und vor der Ausführung verglichen werden.

## 2. Saubere Testinstallation

Auf dem Proxmox-Host als `root` ausführen. `CTID` und `WEB_CIDRS` an die
Testumgebung anpassen:

```bash
(
  set -Eeuo pipefail
  umask 077
  BASE_URL="https://raw.githubusercontent.com/APGW/DPLAcronExportDB/v3.7.4/dist/internal-repository"
  BOOTSTRAP_DIR="$(/usr/bin/mktemp -d -p /root .dpl2acron-bootstrap.XXXXXX)"
  trap '/bin/rm -rf -- "$BOOTSTRAP_DIR"' EXIT
  /usr/bin/curl --disable --proto '=https' --proto-redir '=https' --tlsv1.2 \
    --connect-timeout 10 --max-time 60 --speed-limit 1024 --speed-time 15 \
    --max-filesize 1048576 -fsS "$BASE_URL/bootstrap-proxmox.sh" \
    -o "$BOOTSTRAP_DIR/bootstrap-proxmox.sh"
  printf '%s  %s\n' 'PROXMOX_BOOTSTRAP_SHA256' "$BOOTSTRAP_DIR/bootstrap-proxmox.sh" | \
    /usr/bin/sha256sum --check --status -
  /usr/bin/env -i HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin TERM="${TERM:-xterm}" \
    DPL_BASE_URL="$BASE_URL" CTID=100 WEB_CIDRS="192.168.133.0/24" \
    /bin/bash -p --noprofile --norc "$BOOTSTRAP_DIR/bootstrap-proxmox.sh"
)
```

Der Installer fragt das initiale Administratorkennwort verdeckt ab. Nach
erfolgreicher Installation nennt er die HTTPS-Adresse und den Befehl zum
Kopieren der internen CA.

## 3. Automatisierte Host-Abnahme

Das Abnahmeskript arbeitet gegen den installierten Testcontainer. Es verändert
keine Anwendungsdaten. Für die Restoreprüfung wird kurzzeitig eine separate
MariaDB-Datenbank angelegt und anschließend entfernt.

```bash
(
  set -Eeuo pipefail
  umask 077
  BASE_URL="https://raw.githubusercontent.com/APGW/DPLAcronExportDB/v3.7.4/dist/internal-repository"
  BOOTSTRAP_DIR="$(/usr/bin/mktemp -d -p /root .dpl2acron-acceptance.XXXXXX)"
  trap '/bin/rm -rf -- "$BOOTSTRAP_DIR"' EXIT
  /usr/bin/curl --disable --proto '=https' --proto-redir '=https' --tlsv1.2 \
    --connect-timeout 10 --max-time 60 --speed-limit 1024 --speed-time 15 \
    --max-filesize 1048576 -fsS "$BASE_URL/test/proxmox-acceptance.sh" \
    -o "$BOOTSTRAP_DIR/proxmox-acceptance.sh"
  printf '%s  %s\n' 'ABNAHMESKRIPT_SHA256' "$BOOTSTRAP_DIR/proxmox-acceptance.sh" | \
    /usr/bin/sha256sum --check --status -
  /usr/bin/env -i HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    CTID=100 EXPECTED_VERSION=3.7.4 \
    /bin/bash -p --noprofile --norc "$BOOTSTRAP_DIR/proxmox-acceptance.sh"
)
```

Geprüft werden:

- Debian 13 und unprivilegierter Container
- global aktivierte Proxmox-Firewall, passendes Node-Backend und eingehende CT-Standardrichtlinie `DROP`
- aktive und aktivierte Dienste
- installierte Version 3.7.4
- HTTPS und Zertifikatskette
- Port 8765 ausschließlich auf Loopback und kein externes MariaDB-Listening
- root-kontrollierte Konfigurationsdateien und sichere Cookies
- eingeschränkte MariaDB-Laufzeitrechte
- Datenbank- und Zustandssicherung
- Wiederherstellung des Dumps in eine temporäre Datenbank

Nur die Abschlussmeldung `ABNAHME ERFOLGREICH` gilt als bestandener Lauf.

## 4. Browserprüfung

1. `/etc/dpl-acron/tls/ca.crt` vom Container auf den Testrechner kopieren und
   in dessen vertrauenswürdige Stammzertifikate importieren.
2. `https://CT-IP/` öffnen. Eine HTTP-Adresse oder Port 8765 darf nicht verwendet
   werden.
3. Falsches Kennwort prüfen: Anmeldung muss abgelehnt werden.
4. Mit `admin` anmelden und eine bekannte DPL zunächst als Vorschau laden.
5. DPL aktivieren, Datensatz bearbeiten und die Änderung im Audit prüfen.
6. Einen blockierenden Validierungsfehler erzeugen: Beide Exporte müssen
   serverseitig abgelehnt werden.
7. Fehler korrigieren und beide CSV-Dateien exportieren.
8. Seite in einem zweiten Browser öffnen und einen Konflikt paralleler
   Bearbeitung prüfen.

## 5. Upgradeprüfung einer vorhandenen Testinstanz

Zuerst ein Proxmox-Backup erstellen und den aktuellen Stand notieren:

```bash
vzdump 100 --mode snapshot --compress zstd
pct exec 100 -- python3 /opt/dpl-acron/app.py --version
```

Anschließend das geprüfte 3.7.4-Einstiegsskript in den Container kopieren und
ausführen. Die bei der Erstinstallation gespeicherte Datei
`/etc/dpl-acron/update.env` liefert die interne Repository-Adresse:

```bash
(
  set -Eeuo pipefail
  umask 077
  BASE_URL="https://raw.githubusercontent.com/APGW/DPLAcronExportDB/v3.7.4/dist/internal-repository"
  BOOTSTRAP_DIR="$(/usr/bin/mktemp -d -p /root .dpl2acron-upgrade.XXXXXX)"
  trap '/bin/rm -rf -- "$BOOTSTRAP_DIR"' EXIT
  /usr/bin/curl --disable --proto '=https' --proto-redir '=https' --tlsv1.2 \
    --connect-timeout 10 --max-time 60 --speed-limit 1024 --speed-time 15 \
    --max-filesize 1048576 -fsS "$BASE_URL/ct/dpl-acron.sh" \
    -o "$BOOTSTRAP_DIR/dpl-acron.sh"
  printf '%s  %s\n' 'EINSTIEGSSKRIPT_SHA256' "$BOOTSTRAP_DIR/dpl-acron.sh" | \
    /usr/bin/sha256sum --check --status -
  /usr/bin/curl --disable --proto '=https' --proto-redir '=https' --tlsv1.2 \
    --connect-timeout 10 --max-time 60 --speed-limit 1024 --speed-time 15 \
    --max-filesize 1048576 -fsS "$BASE_URL/test/proxmox-acceptance.sh" \
    -o "$BOOTSTRAP_DIR/proxmox-acceptance.sh"
  printf '%s  %s\n' 'ABNAHMESKRIPT_SHA256' "$BOOTSTRAP_DIR/proxmox-acceptance.sh" | \
    /usr/bin/sha256sum --check --status -
  /usr/sbin/pct push 100 "$BOOTSTRAP_DIR/dpl-acron.sh" /root/dpl-acron-update.sh
  /usr/sbin/pct exec 100 -- /usr/bin/env -i HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    /bin/bash -p --noprofile --norc /root/dpl-acron-update.sh
  /usr/bin/env -i HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    CTID=100 EXPECTED_VERSION=3.7.4 \
    /bin/bash -p --noprofile --norc "$BOOTSTRAP_DIR/proxmox-acceptance.sh"
)
```

Bei einem fehlgeschlagenen Upgrade bleiben Anwendung und Nginx absichtlich
gestoppt. In diesem Fall Protokolle sichern, Ursache beseitigen und den Installer
erneut ausführen. Nicht manuell einen teilweise aktualisierten Dienst starten.
