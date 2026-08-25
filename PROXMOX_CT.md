# Proxmox-CT für den DPL Exportassistenten

## Empfohlene Ausführung

Verwenden Sie einen unprivilegierten Debian-13-CT mit eigener privater IP:

- 2 vCPU
- 2 GB RAM und 512 MB Swap
- mindestens 8 GB Root-Disk
- Autostart aktiviert
- Proxmox-Firewall an der CT-Netzwerkkarte aktiviert
- kein Docker und kein `nesting` erforderlich

Proxmox beschreibt unprivilegierte Container als sicherere Standardvariante und
stellt Debian-Systemvorlagen über `pveam` bereit. Der mitgelieferte
Skriptentwurf `deploy/proxmox-create-ct.sh.example` automatisiert die
Grundkonfiguration, muss aber auf dem Proxmox-Host ausgeführt werden.

## Neuen CT am schnellsten erstellen

Bundle und Skript auf den Proxmox-Host kopieren. Dort die Werte anpassen:

```bash
export CTID=120
export ROOT_STORAGE=local-lvm
export TEMPLATE_STORAGE=local
export BRIDGE=vmbr0
export IP_CONFIG=10.20.30.40/24
export GATEWAY=10.20.30.1
bash deploy/proxmox-create-ct.sh.example
```

Für DHCP kann `IP_CONFIG=dhcp` gesetzt und `GATEWAY` weggelassen werden. Für
einen dauerhaft genutzten Dienst ist eine reservierte oder statische private IP
sinnvoll.

MariaDB im CT installieren und das Projektarchiv hinein kopieren:

```bash
pct exec 120 -- bash -lc 'apt-get update && apt-get install -y mariadb-server'
pct push 120 dpl-acron-debian13.tar.gz /root/dpl-acron-debian13.tar.gz
pct exec 120 -- mkdir -p /root/dpl-acron
pct exec 120 -- tar -xzf /root/dpl-acron-debian13.tar.gz -C /root/dpl-acron
pct exec 120 -- bash /root/dpl-acron/deploy/install-debian13.sh
```

Die ausgegebene private URL anschließend aus einem LAN- oder VPN-Client öffnen.

## Proxmox-Firewall

Vor dem ersten Benutzerzugriff in der Proxmox-Oberfläche:

1. Firewall auf Datacenter-/Node-Ebene gemäß Ihrer bestehenden PVE-Konfiguration aktivieren.
2. Beim CT unter `Hardware → Network Device` die Option `Firewall` aktiv lassen.
3. Beim CT die eingehende Standardrichtlinie auf `DROP` setzen.
4. Regel `IN ACCEPT`, Protokoll `tcp`, Zielport `8765`, Quelle = internes LAN-CIDR anlegen.
5. Eine zweite entsprechende Regel für das VPN-CIDR anlegen.
6. SSH-Port `22` nur aus dem Administrationsnetz erlauben.
7. Keine Regel für MariaDB-Port `3306` anlegen.

Die tatsächlichen LAN- und VPN-CIDRs müssen von der Netzwerkadministration
kommen. Erst die Allow-Regeln testen, danach die Drop-Richtlinie scharf schalten,
damit der Administrationszugang nicht unbeabsichtigt verloren geht.

Es darf weder am Internet-Router noch an einer vorgeschalteten Firewall eine
Weiterleitung für `8765`, `443` oder `3306` auf diesen CT geben. VPN-Clients
greifen über Routing auf die private CT-IP zu.

## Wiederverwendbare CT-Vorlage

Für mehrere Instanzen empfiehlt sich eine Basissystemvorlage ohne
Anwendungsdaten und ohne erzeugte Datenbankkennwörter:

1. Debian-13-CT erstellen.
2. Nur Debian aktualisieren und `mariadb-server` installieren.
3. CT sauber herunterfahren.
4. Auf dem Proxmox-Host `pct template BASIS-CTID` ausführen.
5. Für eine Instanz mit `pct clone BASIS-CTID NEUE-CTID --full 1` klonen.
6. Erst im Klon das DPL-Bundle und `install-debian13.sh` ausführen.

Die bereits vollständig installierte Anwendung sollte nicht ungeprüft in eine
Vorlage umgewandelt werden: Sonst würden MariaDB-Kennwort, Projektdaten und
Sicherungen in jeden Klon übernommen. Der Installer erzeugt diese Werte im Klon
individuell.

## Vorhandenen Debian-CT verwenden

Wenn der Debian-13-CT bereits existiert und MariaDB dort läuft, ist keine neue
Vorlage nötig. Direkt mit der Übertragung und Installation aus
[DEPLOY_DEBIAN13.md](DEPLOY_DEBIAN13.md) beginnen. Die CT-Netzwerkkarte muss
trotzdem `firewall=1` verwenden und die LAN-/VPN-Regeln müssen vor der Nutzung
gesetzt werden.

Offizielle Referenzen:

- [Proxmox Container Toolkit und Systemvorlagen](https://pve.proxmox.com/pve-docs/chapter-pct.html)
- [Proxmox VE Administration Guide](https://pve.proxmox.com/pve-docs/pve-admin-guide.pdf)
