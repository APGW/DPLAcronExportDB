from __future__ import annotations

import csv
from pathlib import Path


def read(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-16", newline="") as source:
        reader = csv.DictReader(source, delimiter="\t")
        rows = list(reader)
        return list(reader.fieldnames or []), rows


generated = Path("exports/braukgraben_neu.csv")
provided = Path(r"C:\Users\alexandru.paraschi\Downloads\braukgraben_neu (2).csv")
generated_headers, generated_rows = read(generated)
provided_headers, provided_rows = read(provided)
key = generated_headers[0]
generated_map = {row[key]: row for row in generated_rows}
provided_map = {row[key]: row for row in provided_rows}
print(f"headers_equal={generated_headers == provided_headers}")
print(f"rows={len(generated_rows)}/{len(provided_rows)}")
print(f"only_generated={sorted(generated_map.keys() - provided_map.keys())}")
print(f"only_provided={sorted(provided_map.keys() - generated_map.keys())}")
for identification in sorted(generated_map.keys() & provided_map.keys()):
    for header in generated_headers:
        if generated_map[identification][header] != provided_map[identification][header]:
            print(
                f"DIFF {identification} | {header} | "
                f"generated={generated_map[identification][header]!r} | "
                f"provided={provided_map[identification][header]!r}"
            )
