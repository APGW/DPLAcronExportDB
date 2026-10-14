from __future__ import annotations

import unittest
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DebianDeploymentTest(unittest.TestCase):
    def test_web_interface_requires_a_login(self) -> None:
        server = (ROOT / "dpl_export" / "web_server.py").read_text(encoding="utf-8")
        page = (ROOT / "dpl_export" / "web" / "index.html").read_text(encoding="utf-8")
        login = (ROOT / "dpl_export" / "web" / "login.html").read_text(encoding="utf-8")
        application = (ROOT / "dpl_export" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("/api/auth/login", server)
        self.assertIn("/api/auth/session", server)
        self.assertIn("_require_session", server)
        self.assertIn("X-CSRF-Token", server)
        self.assertIn("Anmelden", login)
        self.assertIn("auth-logout", page)
        self.assertIn("restoreSession", application)
        self.assertIn("downloadExport", application)
        self.assertIn('response.status === 401', application)
        self.assertIn("Nur Intranet / VPN", page)
        self.assertIn("escapeHtml(detail)", application)
        self.assertIn('method: "POST"', application)

    def test_web_interface_marks_records_edited_in_the_browser_session(self) -> None:
        page = (ROOT / "dpl_export" / "web" / "index.html").read_text(encoding="utf-8")
        application = (ROOT / "dpl_export" / "web" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "dpl_export" / "web" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("session-edited-chip", page)
        self.assertIn("sessionStorage", application)
        self.assertIn("SESSION_IMPORT_KEY", application)
        self.assertIn("markSessionEdited(updated.id)", application)
        self.assertIn("recently-edited", application)
        self.assertIn("tbody tr.recently-edited", styles)
        self.assertIn("session-edited-badge", styles)

    def test_web_interface_uses_the_company_inspired_visual_language(self) -> None:
        page = (ROOT / "dpl_export" / "web" / "index.html").read_text(encoding="utf-8")
        styles = (ROOT / "dpl_export" / "web" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("DPL2ACRON", page)
        self.assertIn("Made with Codex", page)
        self.assertNotIn("DPL Exportassistent", page)
        self.assertIn("--navy: #09141e", styles)
        self.assertIn("--blue: #03ab98", styles)
        self.assertIn("font-family: Manrope", styles)
        self.assertIn("border-bottom: 6px solid var(--blue)", styles)
        self.assertIn(".codex-watermark", styles)
        self.assertIn("opacity: .24", styles)

    def test_pcs7_review_is_available_directly_in_each_table_row(self) -> None:
        application = (ROOT / "dpl_export" / "web" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "dpl_export" / "web" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("row-review-button", application)
        self.assertIn("Als geprüft", application)
        self.assertIn("pcs7Reviewed: !reviewed", application)
        self.assertIn(".row-review-button", styles)
        self.assertIn("select-all-visible", (ROOT / "dpl_export" / "web" / "index.html").read_text(encoding="utf-8"))
        self.assertIn("bulk-review", application)
        self.assertIn("undo-action", application)

    def test_undo_redo_covers_saved_edits_and_keyboard_shortcuts(self) -> None:
        page = (ROOT / "dpl_export" / "web" / "index.html").read_text(encoding="utf-8")
        application = (ROOT / "dpl_export" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("rememberDatapointAction(before, updated,", application)
        self.assertIn('scope === "enabled"', application)
        self.assertIn('document.addEventListener("keydown", handleHistoryShortcut)', application)
        self.assertIn('key === "y"', application)
        self.assertIn("isNativeUndoTarget", application)
        self.assertIn("Strg+Z", page)
        self.assertIn("Strg+Y", page)

    def test_module_management_is_internal_and_ui_only_selects_the_type(self) -> None:
        server = (ROOT / "dpl_export" / "web_server.py").read_text(encoding="utf-8")
        page = (ROOT / "dpl_export" / "web" / "index.html").read_text(encoding="utf-8")
        application = (ROOT / "dpl_export" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("dpl-module-select", page)
        self.assertIn("RÜB, SKU, RRB, HRB, RKB", page)
        self.assertIn("supportedTypes", application)
        self.assertIn("updateModuleSelectionContext", application)
        self.assertIn('select[data-module="rueb"]', styles := (ROOT / "dpl_export" / "web" / "styles.css").read_text(encoding="utf-8"))
        self.assertIn("background: #102631", styles)
        self.assertIn('"X-Anlage-Module": moduleCode', application)
        self.assertNotIn("module-upload-input", page)
        self.assertNotIn("Anlagenmodule verwalten", page)
        self.assertNotIn("/api/modules/import", server)
        self.assertNotIn("/api/modules/{", server)
        self.assertNotIn("uploadModule", application)

    def test_dpl_upload_can_bypass_a_slow_windows_file_dialog(self) -> None:
        page = (ROOT / "dpl_export" / "web" / "index.html").read_text(encoding="utf-8")
        application = (ROOT / "dpl_export" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('type="file" accept=".xlsx" multiple', page)
        self.assertIn("mit Strg+V einfügen", page)
        self.assertIn("function pasteUploadFile(event)", application)
        self.assertIn('document.addEventListener("paste", pasteUploadFile)', application)
        self.assertIn("function selectUploadFiles(fileList)", application)
        self.assertIn("selectUploadFiles(event.dataTransfer.files)", application)
        self.assertIn("upload-file-list", page)
        self.assertIn("Für den nächsten Import ausgewählt", page)
        self.assertIn("upload-start-filename", page)
        self.assertIn("upload-file-state", application)

    def test_improvements_are_available_in_the_web_workflow(self) -> None:
        page = (ROOT / "dpl_export" / "web" / "index.html").read_text(encoding="utf-8")
        application = (ROOT / "dpl_export" / "web" / "app.js").read_text(encoding="utf-8")
        server = (ROOT / "dpl_export" / "web_server.py").read_text(encoding="utf-8")
        self.assertIn("project-select", page)
        self.assertIn("Exportnachweise anzeigen", page)
        self.assertIn("Systemstatus und Datensicherung", page)
        self.assertIn('fetchJson("/api/import-dpl/preview"', application)
        self.assertIn('fetchJson("/api/projects"', application)
        self.assertIn('projectUrl("/api/exports/history?limit=12")', application)
        self.assertIn('"X-Project": state.projectCode', application)
        self.assertIn('parsed.path == "/api/operations"', server)
        self.assertIn('parsed.path == "/api/import-dpl/preview"', server)
        self.assertIn("Cross-Origin-Opener-Policy", server)

    def test_company_logo_is_shipped(self) -> None:
        page = (ROOT / "dpl_export" / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("/assets/agw-logo.png", page)
        self.assertTrue((ROOT / "dpl_export" / "web" / "assets" / "agw-logo.png").is_file())

    def test_deployed_runtime_is_mariadb_only(self) -> None:
        application = (ROOT / "app.py").read_text(encoding="utf-8")
        upload = (ROOT / "dpl_export" / "upload.py").read_text(encoding="utf-8")
        bundle = (ROOT / "deploy" / "build-bundle.ps1").read_text(encoding="utf-8")
        self.assertNotIn('"--database"', application)
        self.assertNotIn("incoming_database", upload)
        self.assertNotIn("import sqlite3", upload)
        self.assertNotIn('    "tests",', bundle)
        self.assertNotIn("ensure_database", application)
        self.assertIn("require_initialized_project(database)", application)

    def test_systemd_service_uses_local_mariadb_configuration(self) -> None:
        service = (ROOT / "deploy" / "dpl-acron.service").read_text(encoding="utf-8")
        self.assertIn("Wants=mariadb.service", service)
        self.assertIn("EnvironmentFile=/etc/dpl-acron/database.env", service)
        self.assertIn("EnvironmentFile=/etc/dpl-acron/auth.env", service)
        self.assertNotIn("dpl.sqlite", service)
        self.assertIn("ReadWritePaths=/var/lib/dpl-acron", service)
        self.assertIn("User=dpl-acron", service)
        self.assertIn("TasksMax=64", service)
        self.assertIn("RestrictNamespaces=true", service)
        self.assertIn("MemoryHigh=1400M", service)
        self.assertIn("MemoryMax=1600M", service)

    def test_installer_installs_and_configures_mariadb_and_connector(self) -> None:
        installer = (ROOT / "deploy" / "install-debian13.sh").read_text(encoding="utf-8")
        self.assertIn("mariadb-server", installer)
        self.assertIn("python3-mariadb-connector", installer)
        self.assertIn("systemctl enable --now mariadb.service", installer)
        self.assertIn("systemctl is-active --quiet mariadb.service", installer)
        self.assertIn("CREATE DATABASE IF NOT EXISTS dpl_acron", installer)
        self.assertIn("CREATE USER IF NOT EXISTS 'dpl_acron'@'localhost'", installer)
        self.assertIn("DPL_DB_DRIVER=mariadb", installer)
        self.assertIn("DPL_BACKUP_LABEL=pre-upgrade", installer)
        self.assertNotIn('"database=$DPL_DB_NAME"', installer)
        self.assertIn('cp -a -- "$source_dir/deploy/." "$install_dir/deploy/"', installer)
        self.assertIn('"$source_dir/PROXMOX_CT.md" "$install_dir/PROXMOX_CT.md"', installer)
        self.assertIn('"$source_dir/PROXMOX_TEST.md" "$install_dir/PROXMOX_TEST.md"', installer)
        self.assertIn("DPL_ADMIN_PASSWORD", installer)
        self.assertIn("DPL_AUTH_PASSWORD_HASH", installer)
        self.assertIn("DPL_TRUST_PROXY", installer)
        self.assertIn("rm -f -- /usr/local/sbin/dpl-acron-user", installer)
        self.assertIn("dpl-acron.pre-intranet-v3", installer)
        self.assertIn("Prüfe Release vor der Installation", installer)
        self.assertIn("Anlagenmodule geprüft", installer)
        self.assertIn('rm -rf -- "$install_dir/tests"', installer)
        self.assertNotIn("python3 -m unittest discover", installer)
        self.assertIn("restore_service_after_failure", installer)
        self.assertIn("dpl-acron-rollback", installer)
        self.assertIn("Datenbank wird auf den Vorzustand", installer)
        self.assertIn("bleibt aus Sicherheitsgründen gestoppt", installer)
        self.assertIn("REVOKE CREATE, ALTER, INDEX, DROP", installer)
        self.assertNotIn("export DPL_ADMIN_PASSWORD", installer)
        self.assertLess(
            installer.index("unset DPL_ADMIN_PASSWORD"),
            installer.index("source_dir="),
        )
        self.assertIn('admin_password_input="${DPL_ADMIN_PASSWORD:-}"', installer)
        self.assertIn('"$config_dir/update.env"', installer)

    def test_intranet_binding_uses_mandatory_https_proxy(self) -> None:
        environment = (ROOT / "deploy" / "dpl-acron.env").read_text(encoding="utf-8")
        nginx = (ROOT / "deploy" / "nginx-dpl-acron.conf.example").read_text(encoding="utf-8")
        installer = (ROOT / "deploy" / "install-debian13.sh").read_text(encoding="utf-8")
        self.assertIn("DPL_HOST=127.0.0.1", environment)
        self.assertIn("DPL_SECURE_COOKIE=1", installer)
        self.assertIn("DPL_TRUST_PROXY=1", installer)
        self.assertIn("systemctl enable nginx.service", installer)
        self.assertIn("systemctl restart nginx.service", installer)
        self.assertLess(installer.index("nginx -t"), installer.index("systemctl restart nginx.service"))
        self.assertIn("proxy_pass http://127.0.0.1:8765", nginx)
        self.assertIn("listen 443 ssl", nginx)
        proxmox = (ROOT / "deploy" / "proxmox-install-lxc.sh").read_text(encoding="utf-8")
        self.assertIn("dport 443", proxmox)
        self.assertNotIn("dport 8765", proxmox)

    def test_proxmox_ct_is_unprivileged_and_firewall_enabled(self) -> None:
        script = (ROOT / "deploy" / "proxmox-install-lxc.sh").read_text(encoding="utf-8")
        guide = (ROOT / "PROXMOX_CT.md").read_text(encoding="utf-8")
        self.assertIn("--unprivileged 1", script)
        self.assertIn("firewall=1", script)
        self.assertIn("--onboot 1", script)
        self.assertNotIn("--start 1", script)
        self.assertLess(script.index("policy_in: DROP"), script.index('pct start "$ctid"'))
        self.assertIn('[[ -n "$web_cidrs" ]]', script)
        self.assertIn('$script_dir/dpl-acron-debian13.tar.gz', script)
        self.assertIn("policy_in: DROP", script)
        self.assertIn('"$SHA256SUM_BIN" "$bundle_file"', script)
        self.assertIn("pveam download", script)
        self.assertIn("pct push", script)
        self.assertIn("install-debian13.sh", script)
        self.assertIn("/api/health", script)
        self.assertIn('RESUME_EXISTING', script)
        self.assertIn('cp -- "$firewall_temp"', script)
        self.assertNotIn('install -m 0640 "$firewall_temp"', script)
        self.assertNotIn('pct exec "$ctid" -- install -d', script)
        self.assertIn('pct exec "$ctid" -- mkdir -p /root/dpl-acron-release', script)
        self.assertIn("report_error", script)
        self.assertIn("Übertrage das geprüfte Release", script)
        self.assertIn("Starte die Debian-13-Anwendungsinstallation", script)
        self.assertLess(script.index("unset DPL_ADMIN_PASSWORD"), script.index("readonly BASH_BIN"))
        self.assertIn("pct template", guide)
        self.assertIn("`443/tcp`", guide)
        self.assertIn("`3306`", guide)
        self.assertIn("Post-Install", guide)

    def test_daily_database_backup_is_enabled(self) -> None:
        installer = (ROOT / "deploy" / "install-debian13.sh").read_text(encoding="utf-8")
        timer = (ROOT / "deploy" / "dpl-acron-backup.timer").read_text(encoding="utf-8")
        backup = (ROOT / "deploy" / "backup-mariadb.sh").read_text(encoding="utf-8")
        self.assertIn("dpl-acron-backup.timer", installer)
        self.assertIn("OnCalendar=*-*-* 02:15:00", timer)
        self.assertIn("--single-transaction", backup)
        self.assertIn('"$database_name"', backup)
        self.assertIn('source "$database_env"', backup)
        self.assertIn("-mtime +14 -delete", backup)
        self.assertIn("backup-status.json", backup)
        self.assertIn('chown root:dpl-acron "$status_temporary"', backup)
        self.assertIn('mktemp -p "$status_dir"', backup)
        self.assertIn('mv -T -- "$status_temporary" "$status_file"', backup)
        self.assertIn("state.tar.gz", backup)
        self.assertIn('install -d -o root -g root -m 0700 "$backup_dir"', backup)
        backup_service = (ROOT / "deploy" / "dpl-acron-backup.service").read_text(encoding="utf-8")
        self.assertIn("/var/lib/dpl-acron-backup", backup_service)

    def test_release_build_creates_a_checksum_and_excludes_runtime_cache(self) -> None:
        bundle = (ROOT / "deploy" / "build-bundle.ps1").read_text(encoding="utf-8")
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("Get-FileHash -Algorithm SHA256", bundle)
        self.assertIn("$absoluteOutput.sha256", bundle)
        self.assertIn("__pycache__/", ignore)
        self.assertIn("dist/backups/", ignore)

    def test_proxmox_acceptance_covers_runtime_security_controls(self) -> None:
        acceptance = (ROOT / "deploy" / "proxmox-acceptance.sh").read_text(encoding="utf-8")
        self.assertIn("EXPECTED_VERSION", acceptance)
        self.assertIn("unprivileged: 1", acceptance)
        self.assertIn("policy_in: DROP", acceptance)
        self.assertIn("https://$ct_ip/api/health", acceptance)
        self.assertIn("127\\.0\\.0\\.1:8765", acceptance)
        self.assertIn("dpl-acron-backup.service", acceptance)
        self.assertIn("SHOW GRANTS FOR CURRENT_USER", acceptance)
        self.assertIn("CREATE DATABASE", acceptance)
        self.assertIn("DROP DATABASE", acceptance)
        self.assertIn("gzip -t", acceptance)
        self.assertIn("tar -tzf", acceptance)
        builder = (ROOT / "deploy" / "build-internal-repository.ps1").read_text(encoding="utf-8")
        self.assertIn('acceptance_test = "test/proxmox-acceptance.sh"', builder)
        self.assertIn("acceptance_test_sha256", builder)
        guide = (ROOT / "PROXMOX_TEST.md").read_text(encoding="utf-8")
        self.assertIn("ABNAHME ERFOLGREICH", guide)
        self.assertIn("EXPECTED_VERSION=3.7.2", guide)
        self.assertIn("EINSTIEGSSKRIPT_SHA256", guide)

    def test_internal_repository_entrypoint_executes_only_verified_project_code(self) -> None:
        template = (ROOT / "deploy" / "community-ct.sh").read_text(encoding="utf-8")
        install = (ROOT / "deploy" / "community-install.sh").read_text(encoding="utf-8")
        builder = (ROOT / "deploy" / "build-internal-repository.ps1").read_text(encoding="utf-8")
        self.assertNotIn("community-scripts/core/main", template)
        self.assertNotIn("source <(", template)
        self.assertIn('APP="dpl-acron"', template)
        self.assertNotIn('exec bash "$installer"', template)
        self.assertNotIn('exec bash "$source_dir/deploy/install-debian13.sh"', template)
        self.assertIn('"$ENV_BIN" -i', template)
        self.assertIn('"$BASH_BIN" -p --noprofile --norc', template)
        self.assertIn("update_mode=1", template)
        self.assertIn('chmod 0700 "$work_dir"', template)
        self.assertLess(template.index('"$SHA256SUM_BIN" --check --status'), template.index('"$BASH_BIN" -p --noprofile --norc "$installer"'))
        self.assertIn("unset DPL_ADMIN_PASSWORD", template)
        self.assertNotIn("export DPL_ADMIN_PASSWORD", template)
        self.assertLess(template.index("unset DPL_ADMIN_PASSWORD"), template.index("update_mode=0"))
        self.assertIn("/etc/dpl-acron/update.env", template)
        self.assertIn('[[ "$update_mode" -eq 0 ]]', template)
        self.assertIn('[[ "$DPL_BASE_URL" == https://*', template)
        self.assertIn('DPL_ALLOW_HTTP', template)
        self.assertIn("@@BUNDLE_SHA256@@", template)
        self.assertIn('source /dev/stdin <<<"$functions_file_path"', install)
        self.assertIn("setting_up_container", install)
        self.assertIn("network_check", install)
        self.assertIn("update_os", install)
        self.assertIn("mariadb-server", install)
        self.assertIn('"$SHA256SUM_BIN" --check --status', install)
        self.assertIn("install-debian13.sh", install)
        self.assertIn("cleanup_lxc", install)
        self.assertIn("build-bundle.ps1", builder)
        self.assertIn("Get-FileHash -Algorithm SHA256", builder)
        self.assertIn('"dpl-acron.sh"', builder)
        self.assertIn('"dpl-acron-install.sh"', builder)
        self.assertIn('"dpl-acron.json"', builder)
        self.assertIn('date_created = "2026-09-07"', builder)
        self.assertIn('architectures = @("amd64")', builder)
        self.assertIn('"release.json"', builder)

    def test_forwardable_installer_is_published_and_verifies_the_entrypoint(self) -> None:
        builder = (ROOT / "deploy" / "build-internal-repository.ps1").read_text(encoding="utf-8")
        entry = (ROOT / "deploy" / "install-proxmox-entry.sh").read_text(encoding="utf-8")

        self.assertIn('Join-Path $repositoryRoot "install-proxmox.sh"', builder)
        self.assertIn("proxmox_entry_sha256", builder)
        self.assertIn("@@COMMUNITY_SHA256@@", entry)
        self.assertIn(
            "https://raw.githubusercontent.com/APGW/DPLAcronExportDB/main/dist/internal-repository",
            entry,
        )
        self.assertIn('"$SHA256SUM_BIN" --check --status', entry)
        self.assertIn('$PYESH_BIN get /cluster/nextid', entry)

    def test_privileged_installers_clear_ambient_execution_state(self) -> None:
        scripts = [
            "install-proxmox-entry.sh",
            "community-ct.sh",
            "proxmox-install-lxc.sh",
            "install-debian13.sh",
        ]
        for name in scripts:
            with self.subTest(script=name):
                content = (ROOT / "deploy" / name).read_text(encoding="utf-8")
                self.assertTrue(content.startswith("#!/bin/bash -p\n"))
                self.assertIn("PYTHONPATH PYTHONHOME PYTHONSTARTUP", content)
                self.assertIn("PATH=/usr/sbin:/usr/bin:/sbin:/bin", content)
        entry = (ROOT / "deploy" / "install-proxmox-entry.sh").read_text(encoding="utf-8")
        community = (ROOT / "deploy" / "community-ct.sh").read_text(encoding="utf-8")
        debian = (ROOT / "deploy" / "install-debian13.sh").read_text(encoding="utf-8")
        self.assertIn('"$PYTHON_BIN" -I -', entry)
        self.assertIn('"$PYTHON_BIN" -I -', community)
        self.assertNotIn("PYTHONPATH=", debian)

        community_install = (ROOT / "deploy" / "community-install.sh").read_text(
            encoding="utf-8"
        )
        self.assertLess(
            community_install.index("builtin export PATH=/usr/sbin:/usr/bin:/sbin:/bin"),
            community_install.index('source /dev/stdin <<<"$functions_file_path"'),
        )
        self.assertLess(
            community_install.index("builtin unset BASH_ENV ENV"),
            community_install.index("setting_up_container"),
        )

    def test_private_repository_ca_is_used_and_persisted(self) -> None:
        community = (ROOT / "deploy" / "community-ct.sh").read_text(encoding="utf-8")
        proxmox = (ROOT / "deploy" / "proxmox-install-lxc.sh").read_text(encoding="utf-8")
        debian = (ROOT / "deploy" / "install-debian13.sh").read_text(encoding="utf-8")

        self.assertIn('DPL_CA_CERT) DPL_CA_CERT="$value"', community)
        self.assertIn('"DPL_CA_CERT=$DPL_CA_CERT"', community)
        self.assertIn('"DPL_UPDATE_CA_CERT_SOURCE=$DPL_CA_CERT"', community)
        self.assertIn('curl_ca_options=(--cacert "$ca_cert")', proxmox)
        self.assertIn('pct push "$ctid" "$ca_cert" "$container_ca_file"', proxmox)
        self.assertIn('"DPL_UPDATE_CA_CERT_SOURCE=$container_ca_file"', proxmox)
        self.assertIn('repository_ca="$config_dir/repository-ca.crt"', debian)
        self.assertIn('update_lines+=("DPL_CA_CERT=$repository_ca")', debian)

    def test_all_release_downloads_are_resource_bounded(self) -> None:
        for name in (
            "install-proxmox-entry.sh",
            "community-ct.sh",
            "community-install.sh",
            "proxmox-install-lxc.sh",
        ):
            with self.subTest(script=name):
                content = (ROOT / "deploy" / name).read_text(encoding="utf-8")
                self.assertIn("--connect-timeout", content)
                self.assertIn("--max-time", content)
                self.assertIn("--speed-limit", content)
                self.assertIn("--speed-time", content)
                self.assertIn("--max-filesize", content)
                self.assertIn("ulimit -f", content)

    def test_forwardable_installer_cidr_policy_is_fail_closed(self) -> None:
        entry = (ROOT / "deploy" / "install-proxmox-entry.sh").read_text(encoding="utf-8")
        match = re.search(
            r'web_cidrs="\$\("\$PYTHON_BIN" -I - "\$web_cidrs" "\$allow_public_cidrs" <<\'PY\'\n(.*?)\nPY\n\)"',
            entry,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        validator = match.group(1)

        def validate(value: str, allow_public: str = "0") -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, "-I", "-", value, allow_public],
                input=validator,
                text=True,
                capture_output=True,
                check=False,
            )

        for value, expected in (
            ("192.168.10.0/24", "192.168.10.0/24"),
            ("10.0.0.0/8, 100.64.0.0/10", "10.0.0.0/8,100.64.0.0/10"),
            ("fd00:1234::/48", "fd00:1234::/48"),
        ):
            with self.subTest(value=value):
                result = validate(value)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(expected, result.stdout.strip())

        for value, allow_public in (
            ("0.0.0.0/0", "1"),
            ("1.2.3.4/24", "1"),
            ("10.0.0.0/8,", "0"),
            ("127.0.0.0/8", "0"),
            ("8.0.0.0/7", "1"),
        ):
            with self.subTest(value=value):
                self.assertNotEqual(0, validate(value, allow_public).returncode)

        public = validate("8.8.8.0/24", "1")
        self.assertEqual(0, public.returncode, public.stderr)
        self.assertEqual("8.8.8.0/24", public.stdout.strip())


if __name__ == "__main__":
    unittest.main()
