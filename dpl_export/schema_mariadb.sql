CREATE TABLE IF NOT EXISTS projects (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    code VARCHAR(100) NOT NULL UNIQUE,
    logical_key VARCHAR(100) NOT NULL DEFAULT '',
    version_number INT UNSIGNED NOT NULL DEFAULT 1,
    is_current TINYINT(1) NOT NULL DEFAULT 1,
    read_only TINYINT(1) NOT NULL DEFAULT 0,
    module_code VARCHAR(32) NOT NULL DEFAULT 'pwk',
    module_version VARCHAR(50) NOT NULL DEFAULT '1.0.0',
    name VARCHAR(255) NOT NULL,
    ba_number VARCHAR(100) NOT NULL,
    acron_prefix VARCHAR(255) NOT NULL,
    acron_group_root VARCHAR(500) NOT NULL,
    provider_group_root VARCHAR(500) NOT NULL,
    acron_filename VARCHAR(255) NOT NULL,
    provider_filename VARCHAR(255) NOT NULL,
    source_dpl TEXT NOT NULL,
    source_acron_template TEXT NOT NULL,
    source_provider_template TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_projects_logical_version ON projects(logical_key, version_number);

CREATE TABLE IF NOT EXISTS imports (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    project_id BIGINT UNSIGNED NOT NULL,
    source_filename VARCHAR(255) NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    selected_rows INT UNSIGNED NOT NULL,
    module_config_json LONGTEXT NOT NULL,
    imported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_import_project_sha (project_id, source_sha256),
    CONSTRAINT fk_import_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS datapoints (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    project_id BIGINT UNSIGNED NOT NULL,
    import_id BIGINT UNSIGNED NOT NULL,
    source_sheet VARCHAR(255) NOT NULL,
    source_row INT UNSIGNED NOT NULL,
    source_number VARCHAR(255),
    marker_color ENUM('yellow', 'orange') NOT NULL,
    has_orange_changes TINYINT(1) NOT NULL DEFAULT 0,
    folder_level1 VARCHAR(500),
    folder_level2 VARCHAR(500),
    class_name VARCHAR(255),
    source_long_name TEXT,
    source_vg_short VARCHAR(500),
    source_pcs7_variable TEXT,
    source_unit VARCHAR(255),
    source_decimals VARCHAR(100),
    source_archive TINYINT(1) NOT NULL DEFAULT 0,
    source_cycle_seconds INT,
    source_delta1 VARCHAR(100),
    source_value_type VARCHAR(255),
    source_data_type VARCHAR(100),
    source_range_min VARCHAR(100),
    source_range_max VARCHAR(100),
    source_acron_long TEXT,
    source_acron_short VARCHAR(500),
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    pcs7_reviewed TINYINT(1) NOT NULL DEFAULT 0,
    pcs7_reviewed_at TIMESTAMP NULL,
    row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_datapoint_source (import_id, source_sheet, source_row),
    KEY idx_datapoints_project (project_id),
    KEY idx_datapoints_marker (project_id, marker_color),
    KEY idx_datapoints_type (project_id, source_value_type),
    CONSTRAINT fk_datapoint_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    CONSTRAINT fk_datapoint_import FOREIGN KEY (import_id) REFERENCES imports(id) ON DELETE CASCADE,
    CONSTRAINT chk_datapoint_enabled CHECK (enabled IN (0, 1)),
    CONSTRAINT chk_datapoint_pcs7_reviewed CHECK (pcs7_reviewed IN (0, 1)),
    CONSTRAINT chk_datapoint_orange_changes CHECK (has_orange_changes IN (0, 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS acron_configs (
    datapoint_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    identification VARCHAR(500) NOT NULL,
    name TEXT NOT NULL,
    group_path TEXT NOT NULL,
    variable_type TINYINT UNSIGNED NOT NULL DEFAULT 0,
    format_out INT NOT NULL DEFAULT 0,
    format_time INT NOT NULL DEFAULT 2,
    calc_type INT,
    pval_from VARCHAR(100),
    pval_to VARCHAR(100),
    formula TEXT,
    pval_handling INT NOT NULL DEFAULT 0,
    unit VARCHAR(255),
    decimals INT NOT NULL DEFAULT 0,
    cycle_seconds INT,
    interval_comp_mode INT,
    daily_comp_mode INT,
    monthly_comp_mode INT,
    yearly_comp_mode INT,
    comment TEXT,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_acron_identification (identification),
    CONSTRAINT fk_acron_datapoint FOREIGN KEY (datapoint_id) REFERENCES datapoints(id) ON DELETE CASCADE,
    CONSTRAINT chk_acron_type CHECK (variable_type BETWEEN 0 AND 3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS provider_configs (
    datapoint_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    include_export TINYINT(1) NOT NULL DEFAULT 0,
    var_name TEXT,
    description TEXT,
    group_path TEXT,
    comment TEXT,
    dbp_type INT NOT NULL DEFAULT 4,
    cycle_interval INT,
    delta1_active TINYINT(1) NOT NULL DEFAULT 0,
    delta1_change VARCHAR(100),
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_provider_datapoint FOREIGN KEY (datapoint_id) REFERENCES datapoints(id) ON DELETE CASCADE,
    CONSTRAINT chk_provider_include CHECK (include_export IN (0, 1)),
    CONSTRAINT chk_provider_delta CHECK (delta1_active IN (0, 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS export_templates (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    project_id BIGINT UNSIGNED NOT NULL,
    kind ENUM('acron', 'provider') NOT NULL,
    delimiter VARCHAR(30) NOT NULL DEFAULT 'tab',
    encoding VARCHAR(50) NOT NULL DEFAULT 'utf-16le-bom',
    UNIQUE KEY uq_export_template_kind (project_id, kind),
    CONSTRAINT fk_template_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS export_template_fields (
    template_id BIGINT UNSIGNED NOT NULL,
    ordinal INT UNSIGNED NOT NULL,
    field_name VARCHAR(255) NOT NULL,
    default_value TEXT,
    PRIMARY KEY (template_id, ordinal),
    UNIQUE KEY uq_template_field (template_id, field_name),
    CONSTRAINT fk_template_field_template FOREIGN KEY (template_id) REFERENCES export_templates(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS audit_log (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    project_id BIGINT UNSIGNED NULL,
    datapoint_id BIGINT UNSIGNED NULL,
    entity VARCHAR(100) NOT NULL,
    field_name VARCHAR(255) NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_by VARCHAR(64) NOT NULL DEFAULT 'system',
    changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_audit_project (project_id, changed_at),
    KEY idx_audit_changed_at (changed_at),
    CONSTRAINT fk_audit_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    CONSTRAINT fk_audit_datapoint FOREIGN KEY (datapoint_id) REFERENCES datapoints(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS export_runs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    project_id BIGINT UNSIGNED NOT NULL,
    import_id BIGINT UNSIGNED NULL,
    kind ENUM('acron', 'provider') NOT NULL,
    filename VARCHAR(255) NOT NULL,
    sha256 CHAR(64) NOT NULL,
    record_count INT UNSIGNED NOT NULL,
    open_errors INT UNSIGNED NOT NULL DEFAULT 0,
    open_warnings INT UNSIGNED NOT NULL DEFAULT 0,
    module_code VARCHAR(32) NOT NULL,
    module_version VARCHAR(50) NOT NULL,
    exported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_export_runs_project (project_id, exported_at),
    CONSTRAINT fk_export_run_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    CONSTRAINT fk_export_run_import FOREIGN KEY (import_id) REFERENCES imports(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS pcs7_imports (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    project_id BIGINT UNSIGNED NOT NULL,
    kind ENUM('parameter', 'signals', 'alarms') NOT NULL,
    source_filename VARCHAR(255) NOT NULL,
    source_sha256 CHAR(64) NOT NULL,
    reference_count INT UNSIGNED NOT NULL,
    imported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_pcs7_import_kind (project_id, kind),
    CONSTRAINT fk_pcs7_import_project FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS pcs7_references (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    import_id BIGINT UNSIGNED NOT NULL,
    endpoint_key VARCHAR(1000) NOT NULL,
    plan_name VARCHAR(255) NOT NULL,
    block_name VARCHAR(255) NOT NULL,
    connection_name VARCHAR(500) NOT NULL,
    data_type VARCHAR(100),
    unit VARCHAR(255),
    comment TEXT,
    source_sheet VARCHAR(255) NOT NULL,
    source_row INT UNSIGNED NOT NULL,
    UNIQUE KEY uq_pcs7_reference_source (import_id, source_sheet, source_row),
    KEY idx_pcs7_reference_key (endpoint_key(255)),
    CONSTRAINT fk_pcs7_reference_import FOREIGN KEY (import_id) REFERENCES pcs7_imports(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
