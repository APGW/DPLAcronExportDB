# Anlagenmodule

Ein Anlagenmodul ist ein austauschbares `.dplmod`-Paket mit genau drei JSON-Dateien:

- `module.json`: Erkennung, Tabellen-/Spaltenzuordnung und optionale Formelregeln
- `database-template.json`: Zuordnung zum normalisierten Datenmodell
- `example-data.json`: Kleine, normalisierte Beispieldaten für Prüfung und Dokumentation

Die Anwendung liefert PWK, RÜ und die gemeinsame RÜB-Liste für
Regenwasserbehandlungsanlagen mit. Die RÜB-Liste gilt für RÜB, SKU, RRB, HRB,
RKB und vergleichbare Anlagen; nur RÜ verwendet weiterhin das eigenständige
RÜ-Modul. Importierte Pakete unter
`$DPL_DATA_DIR/modules/<code>` überschreiben ein gleichnamiges eingebautes Modul sofort.
