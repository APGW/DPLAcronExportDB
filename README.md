# DPL → Acron Export

Der Webdienst liest gelb ausgewählte Datensätze sowie Datensätze mit mindestens
einer orange geänderten Zelle aus einer hochgeladenen DPL, stellt sie zur gemeinsamen
Korrektur bereit und erzeugt getrennte Acron- und
WINCC60-Provider-Exporte.

Die Tabellenansicht unterstützt Einzel- und Mehrfachauswahl. PCS7-Abweichungen
können direkt in der Zeile oder gesammelt als geprüft markiert werden. Ein
kompaktes, ständig sichtbares Undo/Redo-Element hält die letzten 30
Prüfaktionen der aktuellen Browser-Sitzung bereit und schützt dabei über die
Datensatzversion vor dem Überschreiben paralleler Änderungen.

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

Die automatische Vollinstallation eines neuen Debian-13-LXC steht in
[PROXMOX_CT.md](PROXMOX_CT.md). Das Community Script lädt die Debian-Vorlage,
erstellt einen unprivilegierten Container samt Firewall und führt darin das
standardisierte Installskript aus. Für die Proxmox-Browserkonsole kann mit
`deploy/build-internal-repository.ps1` ein intern per HTTPS bereitstellbares
Ein-Befehl-Installationspaket gebaut werden. Der manuelle Weg für vorhandene
Systeme steht in [DEPLOY_DEBIAN13.md](DEPLOY_DEBIAN13.md).
Der konkrete Abnahmelauf mit der Tibaum-DPL ist in [TIBAUM_CHECK.md](TIBAUM_CHECK.md) dokumentiert.

## Datenbank

Der ausgelieferte Dienst verwendet ausschließlich MariaDB. DPL-Importe werden
direkt vorverarbeitet und anschließend in einer MariaDB-Transaktion vollständig
oder gar nicht aktiviert. Es wird keine SQLite-Arbeits- oder Zwischenbank erzeugt.

## Austauschbare Anlagenmodule

Die Importlogik ist nicht mehr auf `PWK_Analogwerte` fest verdrahtet. Mitgeliefert
werden die Module `pwk`, `rue` und `rueb`; RRB und SKU sind bis zum Vorliegen
geeigneter DPLs bewusst nicht aktiviert. Im Webinterface wählt der Benutzer vor
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
- Anlagentyp: vor dem Upload auswählen; eine nicht zur DPL passende Auswahl wird abgewiesen
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
- DPL-Quelle, Acron-Formel und Provider-Variablenname werden auf dieselbe Referenz geprüft
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

Auch die Acron- und Provider-Gruppen werden bei jedem DPL-Import auf die aktuelle
Projektbezeichnung umgestellt.

Der Acron-Export enthält alle aktivierten Datensätze. Der Provider-Export
enthält ausschließlich aktivierte Onlinewerte mit PCS7-Quelle. Hand- und
Rechenwerte bleiben im Acron-Projekt.

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
