# Interner Betrieb auf Debian 13 mit MariaDB

## Zielarchitektur

Anwendung und MariaDB laufen auf demselben Debian-13-System. MariaDB wird nur
über ihren lokalen Unix-Socket angesprochen. Die Webseite ist nach Anmeldung
über die private CT-/VM-Adresse erreichbar.

```text
Mitarbeiter im LAN/VPN → CT-IP:443 (HTTPS) → Nginx → 127.0.0.1:8765 → DPL-Dienst → lokaler MariaDB-Socket
```

Der Webport darf ausschließlich aus den festgelegten Unternehmens- und
VPN-Netzen erreichbar sein. Es darf keine Portweiterleitung aus dem Internet
geben. Für einen Proxmox-CT einschließlich Vorlagen- und Firewallhinweisen siehe
[PROXMOX_CT.md](PROXMOX_CT.md).

## Schnellste Inbetriebnahme

Für einen neuen Proxmox-LXC übernimmt das in [PROXMOX_CT.md](PROXMOX_CT.md)
beschriebene Community Script die vollständige Bereitstellung einschließlich
Debian-Template, lokaler MariaDB, Firewall, Anwendung und Gesundheitsprüfung.

Der folgende manuelle Weg ist für einen bereits vorhandenen Debian-13-LXC oder
eine Debian-13-VM gedacht. MariaDB muss nicht vorinstalliert sein; der Installer
installiert und aktiviert sie bei Bedarf.

Archiv vom Windows-Rechner auf die private IP übertragen:

```powershell
scp .\dist\dpl-acron-debian13.tar.gz BENUTZER@CT-IP:/tmp/
scp .\dist\dpl-acron-debian13.tar.gz.sha256 BENUTZER@CT-IP:/tmp/
ssh BENUTZER@CT-IP
```

Auf Debian installieren:

```bash
cd /tmp
sha256sum --check dpl-acron-debian13.tar.gz.sha256
mkdir -p ~/dpl-acron
tar -xzf /tmp/dpl-acron-debian13.tar.gz -C ~/dpl-acron
cd ~/dpl-acron
sudo bash deploy/install-debian13.sh
```

Bei der ersten Installation fragt der Installer das Administratorkennwort
verdeckt zweimal ab. Der Benutzername ist standardmäßig `admin`. Alternativ kann
das Kennwort automatisiert einmalig über die temporäre Variable
`DPL_ADMIN_PASSWORD` übergeben werden; es darf nicht in Skripte oder das
Repository geschrieben werden.

Danach im LAN oder über das Unternehmens-VPN öffnen:

```text
https://CT-IP/
```

Nach der Installation ist eine Anmeldung erforderlich. Der Installer:

1. installiert und startet bei Bedarf MariaDB,
2. installiert Python und den Debian-Python-Connector,
3. erzeugt die Datenbank `dpl_acron` und `dpl_acron@localhost`,
4. erstellt vor jeder Schemamigration eine `pre-upgrade`-Sicherung,
5. legt das Schema an und importiert die bereitgestellte DPL,
6. richtet das lokale Administratorkonto ein oder erhält dessen Kennwort bei Updates,
7. prüft Programmversion und interne Anlagenmodule vor der Installation,
8. erstellt eine Sicherung des installierten Stands,
9. startet Anwendung und täglichen Sicherungstimer,
10. prüft den Gesundheitsendpunkt.

## Netzwerkfreigabe

Die mitgelieferte Konfiguration bindet das Python-Backend nur an
`127.0.0.1:8765`. Nginx stellt die private CT-/VM-Adresse ausschließlich per
HTTPS bereit. Vor der Nutzung muss die Firewall stehen:

- `443/tcp` eingehend aus den benötigten LAN- und VPN-Subnetzen erlauben
- alle anderen eingehenden Zugriffe auf `443/tcp` verwerfen
- `8765/tcp` nicht im Netzwerk freigeben
- `3306/tcp` nicht freigeben
- `22/tcp` nur aus dem Administrationsnetz zulassen

Die konkreten CIDR-Netze müssen aus der vorhandenen Netz- und VPN-Konfiguration
übernommen werden. Beispiel: `10.20.0.0/16` für LAN und `10.250.0.0/24` für VPN
sind nur Platzhalter und dürfen nicht ungeprüft verwendet werden.

## Verbindliches internes HTTPS

HTTPS schützt Zugangsdaten, DPL-Inhalte und Änderungen während der Übertragung.
Der Installer richtet Nginx, die Loopback-Bindung und eine lokale
Zertifizierungsstelle automatisch ein. Verteilen Sie ausschließlich
`/etc/dpl-acron/tls/ca.crt` an die Clients; `ca.key` darf den Container nicht
verlassen. Eine feste IP oder ein stabiler DNS-Name verhindert, dass ein
DHCP-Wechsel das Serverzertifikat ungültig macht. Nur `443/tcp` darf aus LAN/VPN
erreichbar sein; `8765/tcp` bleibt ausschließlich intern.

## Gleichzeitige Bearbeitung

Jeder Datensatz besitzt eine Bearbeitungsversion. Wurde er nach dem Öffnen von
einer anderen Person gespeichert, lehnt der Server den veralteten
Speicherversuch ab. Die Oberfläche behält die Eingaben sichtbar und bietet an,
den aktuellen Stand zu laden. Es findet kein stilles Überschreiben statt.

Ein neuer DPL-Import ersetzt den Projektbestand transaktional. Bereits geöffnete
Datensätze müssen anschließend neu geöffnet werden. Das Änderungsprotokoll zeigt
Benutzer, Zeitpunkt, Feld sowie alten und neuen Wert.

## Sicherungen

Der Timer erstellt täglich gegen 02:15 Uhr eine konsistente MariaDB-Sicherung
sowie eine root-only Zustandssicherung der Uploads, Module und Konfiguration.
Beide werden 14 Tage aufbewahrt:

```bash
sudo systemctl status dpl-acron-backup.timer
sudo systemctl list-timers dpl-acron-backup.timer
sudo systemctl start dpl-acron-backup.service
sudo ls -lh /var/backups/dpl-acron
```

Wiederherstellung einer ausgewählten Sicherung:

```bash
sudo systemctl stop dpl-acron
sudo bash -c 'source /etc/dpl-acron/database.env; gzip -dc /var/backups/dpl-acron/DATEI.sql.gz | mariadb --defaults-extra-file=/etc/dpl-acron/mariadb-client.cnf "$DPL_DB_NAME"'
sudo systemctl start dpl-acron
```

Die Proxmox-/Unternehmenssicherung des CT ersetzt die lokale 14-Tage-Sicherung
nicht, sondern ergänzt sie.

## Betrieb und Diagnose

```bash
sudo systemctl status dpl-acron
sudo journalctl -u dpl-acron -f
sudo systemctl restart dpl-acron
curl http://127.0.0.1:8765/api/health
```

Erwartete Antwort:

```json
{"status":"ok","version":"3.7.1"}
```

Wichtige Pfade:

- Programm: `/opt/dpl-acron`
- Uploads: `/var/lib/dpl-acron/data/imports`
- optionale PCS7-Exporte: `/var/lib/dpl-acron/data/pcs7`
- intern durch Administratoren bereitgestellte Anlagenmodule: `/var/lib/dpl-acron/data/modules`
- Datenbankzugang: `/etc/dpl-acron/database.env`
- MariaDB-Dump-Konfiguration: `/etc/dpl-acron/mariadb-client.cnf`
- Sicherungen: `/var/backups/dpl-acron`
- Dienst: `/etc/systemd/system/dpl-acron.service`
- Netzkonfiguration: `/etc/default/dpl-acron`

## Aktualisierung

Ein neues Archiv wie bei der Erstinstallation entpacken und den Installer erneut
ausführen. MariaDB-Zugangsdaten und Projektdaten bleiben erhalten. Eine alte
Webdienstkonfiguration wird einmalig als
`/etc/default/dpl-acron.pre-intranet-v3` gesichert; die neue Standardbindung ist
anschließend `127.0.0.1:8765` hinter dem verpflichtenden TLS-Proxy.

Bei einer Community-Script-Installation wird die geprüfte interne
Repository-Adresse root-only in `/etc/dpl-acron/update.env` gespeichert. Das
veröffentlichte CT-Skript kann dadurch spätere Updates innerhalb des Containers
ohne erneute Angabe von `DPL_BASE_URL` oder `WEB_CIDRS` laden und verifizieren.
Schlägt ein Upgrade fehl, werden Datenbank und Programmdateien zurückgesetzt;
Anwendung und Nginx bleiben dabei bewusst gestoppt. Nach Beseitigung der Ursache
ist der Installer erneut auszuführen.
