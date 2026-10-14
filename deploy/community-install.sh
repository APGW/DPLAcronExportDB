#!/bin/bash -p

# Wird von der Community-Scripts-Engine innerhalb des neuen LXC ausgeführt.
functions_file_path="${FUNCTIONS_FILE_PATH:?FUNCTIONS_FILE_PATH wurde nicht übergeben}"
for function_name in $(builtin compgen -A function); do
  builtin unset -f "$function_name"
done
builtin unset BASH_ENV ENV CDPATH GLOBIGNORE PYTHONPATH PYTHONHOME PYTHONSTARTUP \
  LD_PRELOAD LD_LIBRARY_PATH CURL_HOME CURL_CA_BUNDLE SSL_CERT_FILE SSL_CERT_DIR
builtin export IFS=$' \t\n'
builtin export PATH=/usr/sbin:/usr/bin:/sbin:/bin
readonly BASH_BIN=/bin/bash
readonly CURL_BIN=/usr/bin/curl
readonly SHA256SUM_BIN=/usr/bin/sha256sum

# Erst nach der Bereinigung werden die vom Community-Scripts-Framework
# gelieferten Hilfsfunktionen geladen und ausgeführt.
source /dev/stdin <<<"$functions_file_path"
unset functions_file_path
color
verb_ip6
catch_errors
setting_up_container
network_check
update_os

: "${DPL_BASE_URL:?DPL_BASE_URL wurde nicht an den Container übergeben}"
: "${DPL_APP_VERSION:?DPL_APP_VERSION wurde nicht an den Container übergeben}"
: "${DPL_BUNDLE_SHA256:?DPL_BUNDLE_SHA256 wurde nicht an den Container übergeben}"

msg_info "Installiere Systempakete und MariaDB"
$STD apt-get install -y --no-install-recommends \
  ca-certificates curl gzip mariadb-client mariadb-server \
  python3 python3-mariadb-connector util-linux
systemctl enable --now mariadb.service
systemctl is-active --quiet mariadb.service
msg_ok "Systempakete und MariaDB installiert"

curl_options=(--fail --silent --show-error --location)
if [[ "$DPL_BASE_URL" == https://* ]]; then
  curl_options+=(--proto '=https' --tlsv1.2)
  if [[ -n "${DPL_CA_CERT:-}" ]]; then
    [[ "$DPL_CA_CERT" == /* && -f "$DPL_CA_CERT" && ! -L "$DPL_CA_CERT" ]] || {
      msg_error "DPL_CA_CERT nicht gefunden: $DPL_CA_CERT"
      exit 1
    }
    ca_mode="$(/usr/bin/stat -c '%a' -- "$DPL_CA_CERT")"
    [[ "$(/usr/bin/stat -c '%u' -- "$DPL_CA_CERT")" == "0" ]] \
      && (( (8#$ca_mode & 8#022) == 0 )) || {
        msg_error "DPL_CA_CERT muss root gehören und darf nicht gruppen-/weltbeschreibbar sein."
        exit 1
      }
    curl_options+=(--cacert "$DPL_CA_CERT")
  fi
elif [[ "$DPL_BASE_URL" == http://* && "${DPL_ALLOW_HTTP:-0}" == "1" ]]; then
  curl_options+=(--proto '=http')
else
  msg_error "Ungültige Repository-URL oder HTTP wurde nicht ausdrücklich erlaubt."
  exit 1
fi

release_dir="$(mktemp -d)"
trap 'rm -rf -- "$release_dir"' EXIT
bundle="$release_dir/dpl-acron-debian13.tar.gz"

msg_info "Lade und prüfe DPL2ACRON ${DPL_APP_VERSION}"
( ulimit -f 25600; "$CURL_BIN" --disable "${curl_options[@]}" \
  --proto-redir "$( [[ "$DPL_BASE_URL" == https://* ]] && printf '=https' || printf '=http' )" \
  --connect-timeout 10 --max-time 300 --speed-limit 1024 --speed-time 30 \
  --max-filesize 26214400 --output "$bundle" \
  "${DPL_BASE_URL%/}/releases/${DPL_APP_VERSION}/dpl-acron-debian13.tar.gz" )
printf '%s  %s\n' "$DPL_BUNDLE_SHA256" "$bundle" | "$SHA256SUM_BIN" --check --status - \
  || {
    msg_error "SHA-256-Prüfung des DPL-Releases fehlgeschlagen."
    exit 1
  }
mkdir -p "$release_dir/source"
chmod 0700 "$release_dir/source"
tar -xzf "$bundle" -C "$release_dir/source"
msg_ok "DPL-Release geprüft"

msg_info "Installiere Anwendung, Datenbank und Dienste"
/usr/bin/env -i HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
  "DPL_UPDATE_BASE_URL=$DPL_BASE_URL" "DPL_UPDATE_ALLOW_HTTP=${DPL_ALLOW_HTTP:-0}" \
  "DPL_UPDATE_CA_CERT_SOURCE=${DPL_CA_CERT:-}" \
  "$BASH_BIN" -p --noprofile --norc "$release_dir/source/deploy/install-debian13.sh"
systemctl is-active --quiet dpl-acron.service
systemctl is-enabled --quiet dpl-acron-backup.timer
python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8765/api/health", timeout=5).read()'
printf '%s\n' "$DPL_APP_VERSION" > /opt/dpl-acron/.community-script-version
msg_ok "Anwendung, Datenbank und Dienste installiert"

motd_ssh
customize
cleanup_lxc
