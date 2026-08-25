# Interner Betrieb auf Debian 13 mit MariaDB

## Zielarchitektur

Anwendung und MariaDB laufen auf demselben Debian-13-System. MariaDB wird nur
über ihren lokalen Unix-Socket angesprochen. Die Webseite ist ohne Login über
die private CT-/VM-Adresse erreichbar.

```text
Mitarbeiter im LAN/VPN → CT-IP:8765 → DPL-Dienst → lokaler MariaDB-Socket
```

Der Webport darf ausschließlich aus den festgelegten Unternehmens- und
VPN-Netzen erreichbar sein. Es darf keine Portweiterleitung aus dem Internet
geben. Für einen Proxmox-CT einschließlich Vorlagen- und Firewallhinweisen siehe
[PROXMOX_CT.md](PROXMOX_CT.md).

## Schnellste Inbetriebnahme

Voraussetzung ist ein Debian-13-System mit laufender MariaDB:

```bash
sudo systemctl enable --now mariadb
sudo systemctl status mariadb
```

Archiv vom Windows-Rechner auf die private IP übertragen:

```powershell
scp .\dist\dpl-acron-debian13.tar.gz BENUTZER@CT-IP:/tmp/
ssh BENUTZER@CT-IP
```

Auf Debian installieren:

```bash
mkdir -p ~/dpl-acron
tar -xzf /tmp/dpl-acron-debian13.tar.gz -C ~/dpl-acron
cd ~/dpl-acron
sudo bash deploy/install-debian13.sh
```

Danach im LAN oder über das Unternehmens-VPN öffnen:

```text
http://CT-IP:8765/
```

Es ist kein Benutzerkonto erforderlich. Der Installer:

1. prüft die laufende MariaDB,
2. installiert den Debian-Python-Connector,
3. erzeugt die Datenbank `dpl_acron` und `dpl_acron@localhost`,
4. erstellt vor jeder Schemamigration eine `pre-upgrade`-Sicherung,
5. legt das Schema an und importiert die bereitgestellte DPL,
6. entfernt bei einer Aktualisierung alte Login-/Sessiontabellen,
7. führt die automatisierten Tests aus,
8. erstellt eine Sicherung des installierten Stands,
9. startet Anwendung und täglichen Sicherungstimer,
10. prüft den Gesundheitsendpunkt.

## Netzwerkfreigabe

Die mitgelieferte Konfiguration bindet den Dienst an `0.0.0.0:8765`, damit die
private CT-/VM-Adresse erreichbar ist. Vor der Nutzung muss die Firewall stehen:

- `8765/tcp` eingehend aus den benötigten LAN-Subnetzen erlauben
- `8765/tcp` eingehend aus den VPN-Subnetzen erlauben
- alle anderen eingehenden Zugriffe auf `8765/tcp` verwerfen
- `3306/tcp` nicht freigeben
- `22/tcp` nur aus dem Administrationsnetz zulassen

Die konkreten CIDR-Netze müssen aus der vorhandenen Netz- und VPN-Konfiguration
übernommen werden. Beispiel: `10.20.0.0/16` für LAN und `10.250.0.0/24` für VPN
sind nur Platzhalter und dürfen nicht ungeprüft verwendet werden.

## Optional: internes HTTPS

Ein Login wird dadurch nicht ergänzt. HTTPS schützt lediglich DPL-Inhalte und
Änderungen während der Übertragung. Wenn eine interne Zertifizierungsstelle und
ein interner DNS-Name vorhanden sind:

```bash
sudo apt update
sudo apt install -y nginx
sudo cp /opt/dpl-acron/deploy/nginx-dpl-acron.conf.example /etc/nginx/sites-available/dpl-acron
sudoedit /etc/nginx/sites-available/dpl-acron
sudoedit /etc/default/dpl-acron
```

In `/etc/default/dpl-acron` den Python-Dienst nur lokal binden:

```text
DPL_HOST=127.0.0.1
DPL_PORT=8765
```

In der Nginx-Datei den internen Namen und die Zertifikatspfade anpassen. Dann:

```bash
sudo ln -s /etc/nginx/sites-available/dpl-acron /etc/nginx/sites-enabled/dpl-acron
sudo nginx -t
sudo systemctl restart dpl-acron
sudo systemctl reload nginx
```

In diesem Modus nur `443/tcp` aus LAN/VPN zulassen und `8765/tcp` sperren.

## Gleichzeitige Bearbeitung

Jeder Datensatz besitzt eine Bearbeitungsversion. Wurde er nach dem Öffnen von
einer anderen Person gespeichert, lehnt der Server den veralteten
Speicherversuch ab. Die Oberfläche behält die Eingaben sichtbar und bietet an,
den aktuellen Stand zu laden. Es findet kein stilles Überschreiben statt.

Ein neuer DPL-Import ersetzt den Projektbestand transaktional. Bereits geöffnete
Datensätze müssen anschließend neu geöffnet werden. Das Änderungsprotokoll zeigt
Zeitpunkt, Feld sowie alten und neuen Wert, jedoch keine Person.

## Sicherungen

Der Timer erstellt täglich gegen 02:15 Uhr eine konsistente MariaDB-Sicherung
und bewahrt 14 Tage auf:

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
{"status":"ok","backend":"mariadb"}
```

Wichtige Pfade:

- Programm: `/opt/dpl-acron`
- Uploads: `/var/lib/dpl-acron/data/imports`
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
anschließend `0.0.0.0:8765` für den firewallgeschützten Intranetbetrieb.
