# Anlagenmodule

Ein Anlagenmodul ist ein austauschbares `.dplmod`-Paket mit genau drei JSON-Dateien:

- `module.json`: Erkennung, Tabellen-/Spaltenzuordnung und optionale Formelregeln
- `database-template.json`: Zuordnung zum normalisierten Datenmodell
- `example-data.json`: Kleine, normalisierte Beispieldaten für Prüfung und Dokumentation

Die Anwendung liefert PWK, RÜ und RÜB mit. Importierte Pakete unter
`$DPL_DATA_DIR/modules/<code>` überschreiben ein gleichnamiges eingebautes Modul sofort.
RRB und SKU sind bewusst noch nicht angelegt.

