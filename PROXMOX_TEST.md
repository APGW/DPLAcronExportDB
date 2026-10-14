# Proxmox-Abnahme für DPL2ACRON 3.7.1

Diese Abnahme ist für einen ausschließlich zu Testzwecken verwendeten,
unprivilegierten Debian-13-LXC vorgesehen. Vor einem Upgrade einer vorhandenen
Instanz muss ein Proxmox-Backup oder Snapshot erstellt werden.

## 1. Internes Repository bereitstellen

Der vollständige Ordner `dist/internal-repository` wird mit dem Repository auf
GitHub veröffentlicht. Die Basisadresse des geprüften Stands auf `main` lautet:

```text
https://raw.githubusercontent.com/APGW/DPLAcronExportDB/main/dist/internal-repository
```

SHA-256-Wert des Einstiegsskripts muss aus einer vertrauenswürdigen Quelle
übernommen und vor der Ausführung verglichen werden.

## 2. Saubere Testinstallation

Auf dem Proxmox-Host als `root` ausführen. `CTID` und `WEB_CIDRS` an die
Testumgebung anpassen:

```bash
BASE_URL="https://raw.githubusercontent.com/APGW/DPLAcronExportDB/main/dist/internal-repository"
curl -fsS "$BASE_URL/ct/dpl-acron.sh" -o /tmp/dpl-acron.sh
echo "EINSTIEGSSKRIPT_SHA256  /tmp/dpl-acron.sh" | sha256sum --check -

DPL_BASE_URL="$BASE_URL" \
CTID=100 \
WEB_CIDRS="192.168.133.0/24" \
bash /tmp/dpl-acron.sh
```

Der Installer fragt das initiale Administratorkennwort verdeckt ab. Nach
erfolgreicher Installation nennt er die HTTPS-Adresse und den Befehl zum
Kopieren der internen CA.

## 3. Automatisierte Host-Abnahme

Das Abnahmeskript arbeitet gegen den installierten Testcontainer. Es verändert
keine Anwendungsdaten. Für die Restoreprüfung wird kurzzeitig eine separate
MariaDB-Datenbank angelegt und anschließend entfernt.

```bash
curl -fsS "$BASE_URL/test/proxmox-acceptance.sh" -o /tmp/proxmox-acceptance.sh
echo "ABNAHMESKRIPT_SHA256  /tmp/proxmox-acceptance.sh" | sha256sum --check -
CTID=100 EXPECTED_VERSION=3.7.1 bash /tmp/proxmox-acceptance.sh
```

Geprüft werden:

- Debian 13 und unprivilegierter Container
- Proxmox-Firewall mit eingehender Standardrichtlinie `DROP`
- aktive und aktivierte Dienste
- installierte Version 3.7.1
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

## 5. Upgradeprüfung einer vorhandenen 3.7.0-Testinstanz

Zuerst ein Proxmox-Backup erstellen und den aktuellen Stand notieren:

```bash
vzdump 100 --mode snapshot --compress zstd
pct exec 100 -- python3 /opt/dpl-acron/app.py --version
```

Anschließend das geprüfte 3.7.1-Einstiegsskript in den Container kopieren und
ausführen. Die bei der Erstinstallation gespeicherte Datei
`/etc/dpl-acron/update.env` liefert die interne Repository-Adresse:

```bash
pct push 100 /tmp/dpl-acron.sh /tmp/dpl-acron-update.sh
pct exec 100 -- bash /tmp/dpl-acron-update.sh
CTID=100 EXPECTED_VERSION=3.7.1 bash /tmp/proxmox-acceptance.sh
```

Bei einem fehlgeschlagenen Upgrade bleiben Anwendung und Nginx absichtlich
gestoppt. In diesem Fall Protokolle sichern, Ursache beseitigen und den Installer
erneut ausführen. Nicht manuell einen teilweise aktualisierten Dienst starten.
