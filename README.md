# DPL2ACRON

Der Webdienst liest gelb ausgewählte Datensätze sowie Datensätze mit mindestens
einer orange geänderten Zelle aus einer hochgeladenen DPL, stellt sie zur gemeinsamen
Korrektur bereit und erzeugt getrennte ACRON- und
WINCC60-Provider-Exporte.

## Version 3.7.4

- prüft eine DPL vor der Übernahme und zeigt neue, geänderte sowie entfallene Datensätze an
- bewahrt vorherige Projektversionen schreibgeschützt und auswählbar auf
- ordnet PCS7-Prüfdateien und Exporte eindeutig der gewählten Projektversion zu
- protokolliert jeden CSV-Export mit SHA-256-Prüfsumme und Datensatzanzahl
- zeigt Datenbank-, Sicherungs-, Speicher- und Modulstatus direkt im Webinterface
- begrenzt und prüft XLSX-Archive vor der Verarbeitung gegen fehlerhafte oder übergroße Inhalte
- ergänzt restriktive HTTP-Sicherheitsheader für den Intranetbetrieb
- schützt Oberfläche und API durch ein lokales Administratorkonto, Sitzungen und CSRF-Prüfung
- startet die privilegierte Proxmox-Installation mit bereinigter Umgebung und festen Systempfaden
- begrenzt alle Installer-Downloads nach Größe, Laufzeit und Mindestgeschwindigkeit
- normalisiert LAN-/VPN-Netze und lässt öffentliche Netze nur nach ausdrücklicher Freigabe zu
- verwendet für privilegierte Arbeitsdateien ausschließlich ein geprüftes, root-kontrolliertes Verzeichnis
- startet einen neuen Container nur bei global aktivierter Proxmox-Firewall und aktivem Firewall-Backend

Die Tabellenansicht unterstützt Einzel- und Mehrfachauswahl. PCS7-Abweichungen
können direkt in der Zeile oder gesammelt als geprüft markiert werden. Ein
kompaktes, ständig sichtbares Undo/Redo-Element hält die letzten 30
Prüfaktionen der aktuellen Browser-Sitzung bereit und schützt dabei über die
Datensatzversion vor dem Überschreiben paralleler Änderungen.

## Vorgesehener Betrieb

Die Anwendung läuft ausschließlich im Unternehmensnetz auf Debian 13, bevorzugt
in einem unprivilegierten Proxmox-CT. Mitarbeiter greifen über LAN oder
Unternehmens-VPN auf die private CT-Adresse zu. Der Webdienst verlangt eine
Anmeldung mit dem lokalen Administratorkonto. Das Kennwort wird ausschließlich
als gesalzener PBKDF2-Prüfwert gespeichert und bei der Installation verdeckt
abgefragt.

Der Login ergänzt die weiterhin zwingende Abgrenzung im Netzwerk:

- HTTPS-Port `443/tcp` nur aus festgelegten LAN- und VPN-Netzen zulassen; Port `8765` bleibt Loopback-intern
- keine öffentliche DNS-Freigabe und keine Portweiterleitung aus dem Internet
- MariaDB nicht im Netzwerk freigeben; die Anwendung nutzt den lokalen Unix-Socket
- SSH nur aus dem Administrationsnetz erlauben

Enthalten sind weiterhin:

- zentrale MariaDB auf demselben Debian-System
- Änderungsprotokoll mit Benutzer, Zeitstempel sowie altem und neuem Wert
- optimistische Sperren gegen unbemerktes Überschreiben paralleler Änderungen
- transaktionale DPL-Übernahme
- tägliche Datenbank- und Zustandssicherung mit 14 Tagen Aufbewahrung
- verpflichtender interner HTTPS-Reverse-Proxy mit eigener CA

Die automatische Vollinstallation eines neuen Debian-13-LXC steht in
[PROXMOX_CT.md](PROXMOX_CT.md). Das Community Script lädt die Debian-Vorlage,
erstellt einen unprivilegierten Container samt Firewall und führt darin das
standardisierte Installskript aus. Für die Proxmox-Browserkonsole kann mit
`deploy/build-internal-repository.ps1` ein intern per HTTPS bereitstellbares
Ein-Befehl-Installationspaket gebaut werden. Der manuelle Weg für vorhandene
Systeme steht in [DEPLOY_DEBIAN13.md](DEPLOY_DEBIAN13.md).
Die reproduzierbare technische und manuelle Testabnahme steht in
[PROXMOX_TEST.md](PROXMOX_TEST.md).
Der konkrete Abnahmelauf mit der Tibaum-DPL ist in [TIBAUM_CHECK.md](TIBAUM_CHECK.md) dokumentiert.

Für die direkte Neuinstallation auf einem Proxmox-Host wird zusätzlich
`dist/internal-repository/bootstrap-proxmox.sh` veröffentlicht. Dieser
kanonische Bootstrap prüft zuerst `install-proxmox.sh` gegen die beim Build
festgeschriebene SHA-256-Prüfsumme. Erst danach fragt der Installer das erlaubte
LAN-/VPN-Netz ab und wählt eine freie CT-ID.

## Datenbank

Der ausgelieferte Dienst verwendet ausschließlich MariaDB. DPL-Importe werden
direkt vorverarbeitet und anschließend in einer MariaDB-Transaktion vollständig
oder gar nicht aktiviert. Es wird keine SQLite-Arbeits- oder Zwischenbank erzeugt.

## Austauschbare Anlagenmodule

Die Importlogik ist nicht mehr auf `PWK_Analogwerte` fest verdrahtet. Mitgeliefert
werden die Module `pwk`, `rue` und `rueb`. `rueb` bildet die gemeinsame RÜB-Liste
für alle Regenwasserbehandlungsanlagen außer RÜ ab, insbesondere RÜB, SKU, RRB,
HRB und RKB. Es unterstützt sowohl das Bestandslayout als auch das erweiterte
V7.1-Layout. Im Webinterface wählt der Benutzer vor
dem Upload nur den Anlagentyp aus. Der Server prüft diese Auswahl gegen die
Tabellenstruktur der DPL und speichert sie einschließlich Modulversion im aktiven
Projekt.

Ein standardisiertes `.dplmod`-Paket enthält genau:

- `module.json` mit Blatterkennung, Spaltenabbildung, Markierungsbereich und Formelregeln
- `database-template.json` für das gemeinsame normalisierte Datenmodell
- `example-data.json` mit kleinen normalisierten Beispieldaten

Anlagenmodule werden ausschließlich intern durch Administratoren bereitgestellt;
das Webinterface bietet weder Modulimport noch Modulexport an. Interne Module
liegen unter `data/modules` beziehungsweise `/var/lib/dpl-acron/data/modules` und
überschreiben die gleichnamige mitgelieferte Version. Der Modul-Snapshot wird pro
DPL-Import in MariaDB abgelegt, damit später nachvollziehbar bleibt, mit welcher
Zuordnung importiert wurde.

## DPL-Import

Die Oberfläche akzeptiert `.xlsx`-Dateien bis 25 MB einzeln oder gesammelt per
Dateiauswahl, Drag-and-drop und Zwischenablage. Mehrere DPLs landen in einer
Warteschlange und werden bewusst nacheinander aktiviert. Jede ausgewählte Datei
wird vollständig gelesen und validiert, bevor der aktive MariaDB-Datenstand
innerhalb einer Transaktion ersetzt wird.

- erforderliche Blätter: `Info und Historie AnlagenDPL` plus das Analogwertblatt des erkannten Moduls
- unterstützte Analogwertblätter: `PWK_Analogwerte`, `RUE Analogwerte`, `RWB Analogwerte`
- Anlagentyp: PWK, RÜ oder die gemeinsame RÜB-Liste für RÜB/SKU/RRB/HRB/RKB und weitere RWB
- Auswahl: vollflächig gelbe Zeilen und jeder Datensatz mit mindestens einer orange geänderten Zelle
- orange geänderte Zellen: gesonderte Kennzeichnung und Filter im Webinterface
- Rechenwerte: automatische Summenformeln aus den tatsächlich ausgewählten Maschinenwerten
- gültige Uploads: `data/imports` beziehungsweise `/var/lib/dpl-acron/data/imports`
- ungültige oder unvollständige Dateien verändern das aktive Projekt nicht
- bereits geöffnete Datensätze müssen nach einem neuen DPL-Import neu geladen werden

## Optionale PCS7-Prüfung

Im aufklappbaren Bereich „PCS7-Exporte abgleichen“ können Parameter-, Signal-
und Meldungsexporte im XLSX-Format hochgeladen werden. Die Exportart wird anhand
der Spalten automatisch erkannt. Pro Exportart bleibt der zuletzt hochgeladene
Stand aktiv.

- `Plan/Baustein.Anschluss#Value` aus der DPL wird gegen die PCS7-Struktur geprüft
- strukturierte PCS7-Anschlüsse wie `.Value` werden korrekt zugeordnet
- DPL-Quelle, ACRON-Formel und Provider-Variablenname werden auf dieselbe Referenz geprüft
- konkrete Datentypen werden zwischen DPL und PCS7 verglichen
- fehlende Pläne, Bausteine und Anschlüsse erscheinen als markierte Fehler
- die DPL ist der führende Filter: zusätzliche PCS7-Referenzen außerhalb der DPL erzeugen keine Meldung
- Abweichungen können pro DPL-Datensatz manuell als geprüft bestätigt und wieder geöffnet werden
- eine manuell bestätigte Abweichung bleibt sichtbar, blockiert den Export aber nicht mehr
- jeder neue PCS7-Upload setzt manuelle Bestätigungen zurück
- ohne Parameterexport bleibt die Negativprüfung inaktiv, damit Teil-Exporte keine Fehlalarme erzeugen
- PCS7-Dateien liegen unter `data/pcs7` beziehungsweise `/var/lib/dpl-acron/data/pcs7`

Ein neuer DPL-Import ersetzt das Projekt und entfernt damit auch die bisherigen
PCS7-Referenzen. Anschließend müssen die zum neuen DPL-Stand gehörenden
PCS7-Exporte erneut hochgeladen werden.

## Export

Die Oberfläche erzeugt die Dateinamen dynamisch aus der Projektbezeichnung:

- `<Projektbezeichnung> VG.csv`
- `<Projektbezeichnung> PV.csv`

Auch die ACRON- und Provider-Gruppen werden bei jedem DPL-Import auf die aktuelle
Projektbezeichnung umgestellt.

Der ACRON-Export enthält alle aktivierten Datensätze. Der Provider-Export
enthält ausschließlich aktivierte Onlinewerte mit PCS7-Quelle. Hand- und
Rechenwerte bleiben im ACRON-Projekt.

Kommandozeile:

```powershell
python app.py export
```

## Prüfung

```powershell
python -m unittest discover -s tests -v
python app.py --version
python app.py validate
```

Der Release-Build führt die Tests standardmäßig vor dem Packen aus und erzeugt
neben dem Debian-Archiv eine `.sha256`-Datei zur Integritätsprüfung.
