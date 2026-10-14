# Proxmox-LXC für DPL2ACRON

## Empfohlener Aufbau

Der Dienst läuft in einem unprivilegierten Debian-13-LXC mit eigener privater
IP. MariaDB und Anwendung befinden sich im selben Container; Port `3306` wird
nicht ins Netzwerk freigegeben.

- 2 vCPU
- 2 GB RAM und 512 MB Swap
- mindestens 8 GB Root-Disk
- Autostart aktiviert
- Proxmox-Firewall an der CT-Netzwerkkarte aktiviert
- kein Docker, `nesting` oder privilegierter Container erforderlich

Das primäre Verfahren verwendet einen internen, Community-Scripts-kompatiblen
Einstiegspunkt. `ct/dpl-acron.sh` prüft zuerst das vollständige Release und
startet erst danach den darin enthaltenen LXC-Installer. Dadurch wird kein
veränderlicher Fremdcode als Proxmox-root ausgeführt.

## Empfohlen: Ein-Befehl-Installation aus dem internen Repository

Das Projekt erzeugt ein statisches internes Repository, das beispielsweise über
einen internen IIS-, Nginx- oder Artefaktserver per HTTPS bereitgestellt werden
kann. Es enthält die standardisierten Verzeichnisse `ct/`, `install/` und
`json/`, die Metadaten sowie das geprüfte Anwendungsrelease.

Einmalig auf dem Windows-Entwicklungsrechner erstellen:

```powershell
.\deploy\build-internal-repository.ps1
```

Danach den vollständigen Ordner `dist\internal-repository` unverändert unter
einer internen HTTPS-Adresse veröffentlichen, zum Beispiel:

```text
https://software.intern/dpl-acron/
```

In der Proxmox-Browserkonsole als `root` genügt anschließend ein eingefügter
Befehl:

```bash
curl -fsS https://software.intern/dpl-acron/ct/dpl-acron.sh -o /tmp/dpl-acron-community.sh && \
echo "COMMUNITY_SCRIPT_SHA256  /tmp/dpl-acron-community.sh" | sha256sum --check - && \
DPL_BASE_URL="https://software.intern/dpl-acron" \
WEB_CIDRS="192.168.133.0/24,10.250.0.0/24" \
bash /tmp/dpl-acron-community.sh
```

Vor der Containererstellung wird das Administratorkennwort verdeckt zweimal
abgefragt. Der Benutzername ist `admin`. Das Kennwort wird weder in den Befehl
noch in das Repository geschrieben.

Die Adresse, Prüfsumme und Netze sind Beispiele und müssen ersetzt werden. Das
CT-Skript lädt das festgelegte Release ausschließlich aus dem internen
Repository. Die Release-Prüfsumme ist im CT-Skript verankert. Erst nach ihrer
erfolgreichen Prüfung wird der enthaltene Installer ausgeführt.

Bei einer internen Zertifizierungsstelle muss deren CA-Zertifikat auf dem
Proxmox-Host vorhanden sein. Es wird sowohl für den ersten als auch für alle
folgenden Downloads angegeben:

```bash
curl --cacert /usr/local/share/ca-certificates/firma.crt -fsS \
  https://software.intern/dpl-acron/ct/dpl-acron.sh \
  -o /tmp/dpl-acron-community.sh
echo "COMMUNITY_SCRIPT_SHA256  /tmp/dpl-acron-community.sh" | sha256sum --check -
DPL_BASE_URL="https://software.intern/dpl-acron" \
DPL_CA_CERT="/usr/local/share/ca-certificates/firma.crt" \
WEB_CIDRS="192.168.133.0/24,10.250.0.0/24" \
bash /tmp/dpl-acron-community.sh
```

`curl -k` soll nicht verwendet werden, weil dadurch die Serveridentität nicht
mehr geprüft wird.

### Temporär vom lokalen Windows-Rechner bereitstellen

Für einen Test ohne vorhandenen internen Webserver kann der erzeugte Ordner
kurzzeitig im lokalen Netz bereitgestellt werden. Weil dabei HTTP verwendet
wird, muss der Bootstrap vor seiner Ausführung gegen den auf dem
Entwicklungsrechner ermittelten SHA-256-Wert geprüft werden. Erst danach wird
`DPL_ALLOW_HTTP=1` gesetzt. Diese Variante ist nur für ein kontrolliertes lokales
Netz vorgesehen; für den Dauerbetrieb ist internes HTTPS zu verwenden.

Der lokale Server stellt ausschließlich `dist\internal-repository` bereit. In
der Proxmox-Shell wird das Community Script zunächst nach `/tmp` geladen, geprüft und
danach ausgeführt:

```bash
curl -fsS http://WINDOWS-IP:8088/ct/dpl-acron.sh -o /tmp/dpl-acron-community.sh && \
echo "COMMUNITY_SCRIPT_SHA256  /tmp/dpl-acron-community.sh" | sha256sum --check - && \
DPL_ALLOW_HTTP=1 \
DPL_BASE_URL="http://WINDOWS-IP:8088" \
WEB_CIDRS="IHR-LAN/CIDR,IHR-VPN/CIDR" \
bash /tmp/dpl-acron-community.sh
```

Auch diese Variante fragt das Administratorkennwort interaktiv und verdeckt ab.

## Alternative: automatische Vollinstallation mit lokalem Release

Zuerst diese drei Dateien auf den Proxmox-Host kopieren:

- `dpl-acron-debian13.tar.gz`
- `dpl-acron-debian13.tar.gz.sha256`
- `proxmox-install-lxc.sh`

Beispiel vom Windows-Rechner, ausgeführt im Projektverzeichnis:

```powershell
scp .\dist\dpl-acron-debian13.tar.gz root@PVE-IP:/root/
scp .\dist\dpl-acron-debian13.tar.gz.sha256 root@PVE-IP:/root/
scp .\deploy\proxmox-install-lxc.sh root@PVE-IP:/root/
ssh root@PVE-IP
```

Auf dem Proxmox-Host installieren:

```bash
chmod 700 /root/proxmox-install-lxc.sh
CTID=3072 \
WEB_CIDRS="192.168.133.0/24,10.250.0.0/24" \
SSH_CIDRS="192.168.133.0/24" \
SSH_PUBLIC_KEYS="/root/.ssh/authorized_keys" \
bash /root/proxmox-install-lxc.sh
```

Das Skript fragt das Administratorkennwort verdeckt ab und überträgt es nur für
die einmalige Hash-Erzeugung an den Container.

Da Archiv, Prüfsumme und Skript gemeinsam unter `/root` liegen, wird das Bundle
automatisch erkannt. `BUNDLE_PATH` ist nur nötig, wenn es an einem anderen Ort
liegt.

`WEB_CIDRS` ist verpflichtend. Die Beispielnetze müssen durch die tatsächlichen
LAN- und VPN-Netze ersetzt werden. Mehrere Netze werden mit Komma getrennt.
`SSH_CIDRS` und `SSH_PUBLIC_KEYS` sind optional; ohne SSH-Freigabe erfolgt die
Administration über `pct enter 3072` auf dem Proxmox-Host.

Standardmäßig verwendet das Skript DHCP, `local-lvm`, `local` und `vmbr0`. Eine
statische IP und abweichende Speicher lassen sich so setzen:

```bash
CTID=3072 \
CT_HOSTNAME=dpl-acron \
ROOT_STORAGE=local-lvm \
TEMPLATE_STORAGE=local \
BRIDGE=vmbr0 \
IP_CONFIG=192.168.133.130/24 \
GATEWAY=192.168.133.2 \
WEB_CIDRS="192.168.133.0/24,10.250.0.0/24" \
SSH_CIDRS="192.168.133.0/24" \
bash /root/proxmox-install-lxc.sh
```

Das Release kann alternativ von einer internen HTTPS-Adresse geladen werden.
Die erwartete Prüfsumme muss dabei ausdrücklich angegeben werden:

```bash
CTID=3072 \
WEB_CIDRS="192.168.133.0/24,10.250.0.0/24" \
BUNDLE_URL="https://software.intern/dpl-acron-debian13.tar.gz" \
BUNDLE_SHA256="HIER_64_HEX_ZEICHEN_EINTRAGEN" \
bash /root/proxmox-install-lxc.sh
```

Das Skript bricht ab, wenn die CT-ID bereits vorhanden ist, die Prüfsumme nicht
passt, kein Debian-13-Template verfügbar ist oder der Container die
Debian-Paketquellen nicht erreicht. Ein nach der Erstellung auftretender Fehler
lässt den Container zur Diagnose bestehen; er wird nicht automatisch gelöscht.
Ein eindeutig als `dpl-acron` markierter, unprivilegierter Debian-Container kann
nach einem abgebrochenen Lauf mit `RESUME_EXISTING=1` fortgesetzt werden. Er muss
dazu gestoppt sein. Fremde oder laufende Container werden nicht übernommen.

## Einordnung zu Proxmox VE Community Scripts

Die Community-Scripts-Dokumentation trennt dieselben zwei Ausführungsebenen:

- `ct/*.sh` läuft auf dem Proxmox-Host und erstellt den Container.
- `install/*.sh` läuft innerhalb des Containers und richtet die Anwendung ein.

In diesem Projekt entsprechen dem:

| Community-Scripts-Rolle | Datei dieses Projekts | Ausführungsort |
| --- | --- | --- |
| Container Script | `deploy/community-ct.sh`, veröffentlicht als `ct/dpl-acron.sh` | Proxmox-Host |
| Installation Script | `deploy/community-install.sh`, veröffentlicht als `install/dpl-acron-install.sh` | Debian-13-LXC |
| Anwendungsinstallation | `deploy/install-debian13.sh` im geprüften Release | Debian-13-LXC |
| Metadaten | `json/dpl-acron.json` | internes Repository |

Der veröffentlichte Einstiegspunkt bewahrt die Ein-Befehl-Bedienung, ruft aber
keine veränderliche Community-Scripts-Engine auf. Containererstellung, Firewall
und Installation stammen aus demselben per SHA-256 geprüften Release.

## Was ist ein Post-Install-Skript?

Der Begriff wird in zwei verwandten Bedeutungen verwendet:

1. Debian-Pakete können ein `postinst`-Maintainer-Skript enthalten, das nach dem
   Entpacken und Konfigurieren des Pakets ausgeführt wird.
2. Bei der Systembereitstellung bezeichnet „Post-Install“ meist ein Skript, das
   nach Erstellung des Betriebssystems beziehungsweise Containers die konkrete
   Anwendung einrichtet.

Im Sprachgebrauch der Community-Scripts-Dokumentation heißt diese zweite Stufe
„Installation Script“. Für dieses Projekt gilt entsprechend:

```text
Proxmox-Host
└─ ct/dpl-acron.sh              Community-Scripts-Dialog und LXC-Erstellung
   └─ install/dpl-acron-install.sh  Installation innerhalb des LXC
      └─ install-debian13.sh    Anwendung und lokale MariaDB
      ├─ installiert Python, MariaDB und Connector
      ├─ erzeugt lokalen DB-Benutzer und Schema
      ├─ installiert Anwendung und systemd-Dienste
      ├─ aktiviert tägliche Datenbanksicherungen
      └─ prüft abschließend /api/health
```

Das Post-Install-Skript ist wiederholt ausführbar und dient zugleich für spätere
Updates. Zugangsdaten und Projektdaten bleiben dabei erhalten; vor einer
Schemamigration wird eine MariaDB-Sicherung erstellt. Das Skript wird aus einem
lokal geprüften Release aufgerufen und nicht unkontrolliert über `curl | bash`.

## Firewall und Erreichbarkeit

Das Hostskript legt `/etc/pve/firewall/<CTID>.fw` mit eingehender
Standardrichtlinie `DROP` an. Es erlaubt:

- ICMP für Diagnose
- bei DHCP die notwendige DHCP-Antwort
- `443/tcp` ausschließlich aus `WEB_CIDRS`
- optional `22/tcp` ausschließlich aus `SSH_CIDRS`

Die Firewall muss zusätzlich auf Datacenter-/Node-Ebene gemäß der vorhandenen
Proxmox-Konfiguration aktiviert sein. Es darf weder am Internet-Router noch an
einer vorgeschalteten Firewall eine öffentliche Weiterleitung für `443`
oder `3306` auf diesen Container geben. VPN-Clients greifen per Routing auf die
private CT-IP zu.

## Wiederverwendbare CT-Vorlage

Eine Basissystemvorlage darf keine Anwendungsdaten oder erzeugten
Datenbankkennwörter enthalten:

1. Debian-13-LXC erstellen.
2. Nur das Basissystem aktualisieren.
3. LXC sauber herunterfahren.
4. Auf dem Proxmox-Host `pct template BASIS-CTID` ausführen.
5. Mit `pct clone BASIS-CTID NEUE-CTID --full 1` klonen.
6. Erst im Klon das geprüfte Release und `install-debian13.sh` ausführen.

Für eine einzelne Instanz ist das vollständige Installationsskript einfacher
und verhindert, dass Kennwörter, Projektdaten oder Sicherungen mitgeklont
werden.

## Vorhandenen Debian-13-LXC verwenden

In einem bereits vorhandenen Container reicht die Installation aus
[DEPLOY_DEBIAN13.md](DEPLOY_DEBIAN13.md). MariaDB muss nicht vorinstalliert
sein; das Post-Install-Skript installiert und aktiviert sie bei Bedarf. Die
Netzwerkfreigabe muss dennoch auf LAN und VPN begrenzt werden.

Offizielle Referenzen:

- [Proxmox `pct` – Container Toolkit](https://pve.proxmox.com/pve-docs-9-beta/pct.1.html)
- [Debian-Wiki: MariaDB](https://wiki.debian.org/Mariadb)
- [Debian Policy: Maintainer- und `postinst`-Skripte](https://www.debian.org/doc/debian-policy/ch-maintainerscripts.html)
- [Community Scripts: Container Scripts](https://community-scripts.org/docs/ct/readme)
- [Community Scripts: Installation Scripts](https://community-scripts.org/docs/install/readme)
