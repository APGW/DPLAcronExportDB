#!/bin/bash -p
set -Eeuo pipefail

# Dieser beim Release-Bau erzeugte Bootstrap ist die kanonische äußere
# Vertrauensgrenze für eine Proxmox-Neuinstallation. Er führt den eigentlichen
# Installer ausschließlich nach erfolgreicher Prüfung des fest eingebetteten
# SHA-256-Werts aus.

base_url="${DPL_BASE_URL:-https://raw.githubusercontent.com/APGW/DPLAcronExportDB/main/dist/internal-repository}"
entry_sha256="9bff1b3cef72c4e9ea98cd97131e23c4d49dd2a380055862069b833d60f3fb63"
ca_cert="${DPL_CA_CERT:-}"
ctid="${CTID:-}"
web_cidrs="${WEB_CIDRS:-}"
allow_public_cidrs="${DPL_ALLOW_PUBLIC_CIDRS:-0}"

for function_name in $(builtin compgen -A function); do
  builtin unset -f "$function_name"
done
builtin unset BASH_ENV ENV CDPATH GLOBIGNORE PYTHONPATH PYTHONHOME PYTHONSTARTUP \
  LD_PRELOAD LD_LIBRARY_PATH CURL_HOME CURL_CA_BUNDLE SSL_CERT_FILE SSL_CERT_DIR
builtin export IFS=$' \t\n'
builtin export PATH=/usr/sbin:/usr/bin:/sbin:/bin

readonly BASH_BIN=/bin/bash
readonly CHMOD_BIN=/bin/chmod
readonly CURL_BIN=/usr/bin/curl
readonly ENV_BIN=/usr/bin/env
readonly MKTEMP_BIN=/usr/bin/mktemp
readonly RM_BIN=/bin/rm
readonly SHA256SUM_BIN=/usr/bin/sha256sum
readonly STAT_BIN=/usr/bin/stat

fail() {
  echo "Fehler: $*" >&2
  exit 1
}

[[ "${EUID:-99999}" -eq 0 ]] || fail "Der Bootstrap muss als root auf Proxmox VE laufen."
for required_tool in "$BASH_BIN" "$CHMOD_BIN" "$CURL_BIN" "$ENV_BIN" \
  "$MKTEMP_BIN" "$RM_BIN" "$SHA256SUM_BIN" "$STAT_BIN"; do
  [[ -x "$required_tool" ]] || fail "Erforderliches Systemprogramm fehlt: $required_tool"
  tool_owner="$("$STAT_BIN" -Lc '%u' "$required_tool")"
  tool_mode="$("$STAT_BIN" -Lc '%a' "$required_tool")"
  [[ "$tool_owner" == "0" && $((8#$tool_mode & 022)) -eq 0 ]] \
    || fail "Unsichere Eigentumsrechte am Systemprogramm: $required_tool"
done

base_url="${base_url%/}"
[[ "$base_url" == https://* ]] || fail "DPL_BASE_URL muss HTTPS verwenden."
[[ "$entry_sha256" =~ ^[a-f0-9]{64}$ ]] || fail "Die eingebettete Installer-Prüfsumme ist ungültig."

curl_ca_options=()
if [[ -n "$ca_cert" ]]; then
  [[ "$ca_cert" == /* && -f "$ca_cert" && ! -L "$ca_cert" ]] \
    || fail "DPL_CA_CERT muss eine absolute, reguläre CA-Datei sein."
  ca_owner="$("$STAT_BIN" -Lc '%u' "$ca_cert")"
  ca_mode="$("$STAT_BIN" -Lc '%a' "$ca_cert")"
  [[ "$ca_owner" == "0" && $((8#$ca_mode & 022)) -eq 0 ]] \
    || fail "DPL_CA_CERT muss root gehören und darf nicht gruppen-/weltbeschreibbar sein."
  curl_ca_options=(--cacert "$ca_cert")
fi

work_dir="$("$MKTEMP_BIN" -d)"
"$CHMOD_BIN" 0700 "$work_dir"
trap '"$RM_BIN" -rf -- "$work_dir"' EXIT
installer="$work_dir/install-proxmox.sh"

echo "Lade und prüfe den DPL2ACRON-Proxmox-Installer ..."
( ulimit -f 1024; "$CURL_BIN" --disable --fail --silent --show-error --location \
  --proto '=https' --proto-redir '=https' --tlsv1.2 --connect-timeout 10 \
  --max-time 60 --speed-limit 1024 --speed-time 15 --max-filesize 1048576 \
  "${curl_ca_options[@]}" --output "$installer" "$base_url/install-proxmox.sh" )
printf '%s  %s\n' "$entry_sha256" "$installer" | "$SHA256SUM_BIN" --check --status - \
  || fail "SHA-256-Prüfung fehlgeschlagen; der Installer wird nicht ausgeführt."

clean_environment=(
  "$ENV_BIN" -i HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin
  "DPL_BASE_URL=$base_url"
  "DPL_ALLOW_PUBLIC_CIDRS=$allow_public_cidrs"
)
if [[ -n "$ctid" ]]; then
  clean_environment+=("CTID=$ctid")
fi
if [[ -n "$web_cidrs" ]]; then
  clean_environment+=("WEB_CIDRS=$web_cidrs")
fi
if [[ -n "$ca_cert" ]]; then
  clean_environment+=("DPL_CA_CERT=$ca_cert")
fi
if [[ -n "${TERM:-}" ]]; then
  clean_environment+=("TERM=$TERM")
fi

"${clean_environment[@]}" "$BASH_BIN" -p --noprofile --norc "$installer"
