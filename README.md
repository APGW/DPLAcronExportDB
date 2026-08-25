# DPL → Acron Export

Der Webdienst liest gelb oder orange markierte Datensätze aus einer hochgeladenen
DPL, stellt sie zur gemeinsamen Korrektur bereit und erzeugt getrennte Acron- und
WINCC60-Provider-Exporte.

## Vorgesehener Betrieb

Die Anwendung läuft ausschließlich im Unternehmensnetz auf Debian 13, bevorzugt
in einem unprivilegierten Proxmox-CT. Mitarbeiter greifen über LAN oder
Unternehmens-VPN auf die private CT-Adresse zu. Es gibt bewusst kein Login.

Die Abgrenzung erfolgt deshalb zwingend im Netzwerk:

- Webport `8765/tcp` nur aus festgelegten LAN- und VPN-Netzen zulassen
- keine öffentliche DNS-Freigabe und keine Portweiterleitung aus dem Internet
- MariaDB nicht im Netzwerk freigeben; die Anwendung nutzt den lokalen Unix-Socket
- SSH nur aus dem Administrationsnetz erlauben

Enthalten sind weiterhin:

- zentrale MariaDB auf demselben Debian-System
- Zeitstempel-Protokoll mit altem und neuem Wert, ohne Personenbezug
- optimistische Sperren gegen unbemerktes Überschreiben paralleler Änderungen
- transaktionale DPL-Übernahme
- tägliche MariaDB-Sicherung mit 14 Tagen Aufbewahrung
- optionaler interner HTTPS-Reverse-Proxy

Die schnellste Installation steht in [DEPLOY_DEBIAN13.md](DEPLOY_DEBIAN13.md).
Für neue Container und CT-Vorlagen siehe [PROXMOX_CT.md](PROXMOX_CT.md).

## Lokale Entwicklung unter Windows

SQLite bleibt als Entwicklungs- und Testdatenbank verfügbar:

```powershell
python app.py --database data/dpl.sqlite init
python app.py --database data/dpl.sqlite serve
```

Danach `http://127.0.0.1:8765` öffnen. Die Oberfläche startet direkt ohne
Anmeldung.

## DPL-Import

Die Oberfläche akzeptiert `.xlsx`-Dateien bis 25 MB. Eine Datei wird zuerst in
einer isolierten SQLite-Prüfdatenbank vollständig validiert. Erst anschließend
wird der aktive MariaDB-Datenstand innerhalb einer Transaktion ersetzt.

- erforderliche Blätter: `Info und Historie AnlagenDPL` und `PWK_Analogwerte`
- Auswahl: ausschließlich gelb oder orange markierte Datensätze
- gültige Uploads: `data/imports` beziehungsweise `/var/lib/dpl-acron/data/imports`
- ungültige oder unvollständige Dateien verändern das aktive Projekt nicht
- bereits geöffnete Datensätze müssen nach einem neuen DPL-Import neu geladen werden

## Export

Die Oberfläche erzeugt:

- `braukgraben_neu.csv`
- `shhamm_provider1_WINCC60.csv`

Der Acron-Export enthält alle aktivierten Datensätze. Der Provider-Export
enthält ausschließlich aktivierte Onlinewerte mit PCS7-Quelle. Hand- und
Rechenwerte bleiben im Acron-Projekt.

Kommandozeile:

```powershell
python app.py --database data/dpl.sqlite export
```

## Prüfung

```powershell
python -m unittest discover -s tests -v
python app.py --database data/dpl.sqlite validate
```
