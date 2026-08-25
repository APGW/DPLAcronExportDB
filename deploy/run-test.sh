#!/usr/bin/env bash
set -Eeuo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
host="${DPL_HOST:-127.0.0.1}"
port="${DPL_PORT:-8765}"
database="$project_dir/data/dpl-test.sqlite"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 fehlt. Installieren Sie es mit: sudo apt install python3" >&2
  exit 1
fi

python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else "Python 3.11 oder neuer ist erforderlich.")'

cd "$project_dir"
python3 app.py --database "$database" init
echo "DPL Exportassistent startet auf http://$host:$port"
echo "Die Oberfläche öffnet ohne Anmeldung. Standardmäßig ist sie nur lokal gebunden."
echo "Beenden mit Strg+C"
exec python3 app.py --database "$database" --data-dir "$project_dir/data" serve --host "$host" --port "$port"
