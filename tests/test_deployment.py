from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DebianDeploymentTest(unittest.TestCase):
    def test_web_interface_has_no_login_system(self) -> None:
        server = (ROOT / "dpl_export" / "web_server.py").read_text(encoding="utf-8")
        page = (ROOT / "dpl_export" / "web" / "index.html").read_text(encoding="utf-8")
        application = (ROOT / "dpl_export" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("/api/auth/", server)
        self.assertNotIn("login-screen", page)
        self.assertNotIn("restoreSession", application)
        self.assertIn("Nur Intranet / VPN", page)

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
        styles = (ROOT / "dpl_export" / "web" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("--navy: #09141e", styles)
        self.assertIn("--blue: #03ab98", styles)
        self.assertIn("font-family: Manrope", styles)
        self.assertIn("border-bottom: 6px solid var(--blue)", styles)

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
        self.assertNotIn("dpl.sqlite", service)
        self.assertIn("ReadWritePaths=/var/lib/dpl-acron", service)
        self.assertIn("User=dpl-acron", service)
        self.assertIn("TasksMax=64", service)
        self.assertIn("RestrictNamespaces=true", service)

    def test_installer_configures_existing_mariadb_and_connector(self) -> None:
        installer = (ROOT / "deploy" / "install-debian13.sh").read_text(encoding="utf-8")
        self.assertIn("python3-mariadb-connector", installer)
        self.assertIn("systemctl is-active --quiet mariadb.service", installer)
        self.assertIn("CREATE DATABASE IF NOT EXISTS dpl_acron", installer)
        self.assertIn("CREATE USER IF NOT EXISTS 'dpl_acron'@'localhost'", installer)
        self.assertIn("DPL_DB_DRIVER=mariadb", installer)
        self.assertIn("DPL_BACKUP_LABEL=pre-upgrade", installer)
        self.assertNotIn('"database=$DPL_DB_NAME"', installer)
        self.assertIn('cp -a -- "$source_dir/deploy/." "$install_dir/deploy/"', installer)
        self.assertIn('"$source_dir/PROXMOX_CT.md" "$install_dir/PROXMOX_CT.md"', installer)
        self.assertNotIn("create-user --username admin", installer)
        self.assertIn("rm -f -- /usr/local/sbin/dpl-acron-user", installer)
        self.assertIn("dpl-acron.pre-intranet-v3", installer)
        self.assertIn("Prüfe Release vor der Installation", installer)
        self.assertIn("Anlagenmodule geprüft", installer)
        self.assertIn('rm -rf -- "$install_dir/tests"', installer)
        self.assertNotIn("python3 -m unittest discover", installer)
        self.assertIn("restore_service_after_failure", installer)

    def test_intranet_binding_and_optional_https_proxy_are_prepared(self) -> None:
        environment = (ROOT / "deploy" / "dpl-acron.env").read_text(encoding="utf-8")
        nginx = (ROOT / "deploy" / "nginx-dpl-acron.conf.example").read_text(encoding="utf-8")
        self.assertIn("DPL_HOST=0.0.0.0", environment)
        self.assertNotIn("DPL_SECURE_COOKIE", environment)
        self.assertIn("proxy_pass http://127.0.0.1:8765", nginx)
        self.assertIn("listen 443 ssl", nginx)

    def test_proxmox_ct_is_unprivileged_and_firewall_enabled(self) -> None:
        script = (ROOT / "deploy" / "proxmox-create-ct.sh.example").read_text(encoding="utf-8")
        guide = (ROOT / "PROXMOX_CT.md").read_text(encoding="utf-8")
        self.assertIn("--unprivileged 1", script)
        self.assertIn("firewall=1", script)
        self.assertIn("--onboot 1", script)
        self.assertIn("pct template", guide)
        self.assertIn("Zielport `8765`", guide)
        self.assertIn("MariaDB-Port `3306`", guide)

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

    def test_release_build_creates_a_checksum_and_excludes_runtime_cache(self) -> None:
        bundle = (ROOT / "deploy" / "build-bundle.ps1").read_text(encoding="utf-8")
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("Get-FileHash -Algorithm SHA256", bundle)
        self.assertIn("$absoluteOutput.sha256", bundle)
        self.assertIn("__pycache__/", ignore)
        self.assertIn("dist/backups/", ignore)


if __name__ == "__main__":
    unittest.main()
