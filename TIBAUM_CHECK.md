# Konkreter Tibaum-Abnahmelauf

Geprüfte Datei: `2025-02-06 DPL_PWK HAM-Am Tibaum (BF) (SH).xlsx`

Ergebnis des vollständigen DPL-, Datenbank- und Exportlaufs:

- Projektname: `PWK HAM-Am Tibaum (BF) (SH)`
- 58 ausgewählte DPL-Datensätze
- 48 Providerwerte
- 10 reine Acron-Werte
- 36 Datensätze mit mindestens einer orange geänderten Zelle
- Acron-Datei: `PWK HAM-Am Tibaum (BF) (SH) VG.csv`
- Provider-Datei: `PWK HAM-Am Tibaum (BF) (SH) PV.csv`

Die fachliche Prüfung meldet zwei konkrete Fehler:

- `702501STDA_PWK_M1_ZFSY` – Messbereich von muss kleiner als bis sein.
- `702501STDA_PWK_M2_ZFSY` – Messbereich von muss kleiner als bis sein.

Beide Fehler werden im Interface als zu korrigierende DPL-Datensätze angezeigt
und blockieren den Export, bis der Messbereich korrigiert wurde. Der Lauf zeigt
damit konkret, dass die DPL das führende Archivierungsfilter bleibt und die
Prüfung nicht aus zusätzlichen PCS7-Datensätzen künstliche Fehler erzeugt.
