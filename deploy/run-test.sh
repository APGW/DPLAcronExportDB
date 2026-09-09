#!/usr/bin/env bash
set -Eeuo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 fehlt. Installieren Sie es mit: sudo apt install python3" >&2
  exit 1
fi

python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else "Python 3.11 oder neuer ist erforderlich.")'

set -a
source /etc/dpl-acron/database.env
set +a
cd "$project_dir"
python3 app.py validate
