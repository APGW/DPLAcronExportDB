PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    ba_number TEXT NOT NULL,
    acron_prefix TEXT NOT NULL,
    acron_group_root TEXT NOT NULL,
    provider_group_root TEXT NOT NULL,
    acron_filename TEXT NOT NULL,
    provider_filename TEXT NOT NULL,
    source_dpl TEXT NOT NULL,
    source_acron_template TEXT NOT NULL,
    source_provider_template TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_filename TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    selected_rows INTEGER NOT NULL,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, source_sha256)
);

CREATE TABLE IF NOT EXISTS datapoints (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    import_id INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    source_sheet TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    source_number TEXT,
    marker_color TEXT NOT NULL CHECK(marker_color IN ('yellow', 'orange')),
    folder_level1 TEXT,
    folder_level2 TEXT,
    class_name TEXT,
    source_long_name TEXT,
    source_vg_short TEXT,
    source_pcs7_variable TEXT,
    source_unit TEXT,
    source_decimals TEXT,
    source_archive INTEGER NOT NULL DEFAULT 0,
    source_cycle_seconds INTEGER,
    source_delta1 TEXT,
    source_value_type TEXT,
    source_data_type TEXT,
    source_range_min TEXT,
    source_range_max TEXT,
    source_acron_long TEXT,
    source_acron_short TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    row_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(import_id, source_sheet, source_row)
);

CREATE INDEX IF NOT EXISTS idx_datapoints_project ON datapoints(project_id);
CREATE INDEX IF NOT EXISTS idx_datapoints_marker ON datapoints(project_id, marker_color);
CREATE INDEX IF NOT EXISTS idx_datapoints_type ON datapoints(project_id, source_value_type);

CREATE TABLE IF NOT EXISTS acron_configs (
    datapoint_id INTEGER PRIMARY KEY REFERENCES datapoints(id) ON DELETE CASCADE,
    identification TEXT NOT NULL,
    name TEXT NOT NULL,
    group_path TEXT NOT NULL,
    variable_type INTEGER NOT NULL DEFAULT 0 CHECK(variable_type BETWEEN 0 AND 3),
    format_out INTEGER NOT NULL DEFAULT 0,
    format_time INTEGER NOT NULL DEFAULT 2,
    calc_type INTEGER,
    pval_from TEXT,
    pval_to TEXT,
    formula TEXT,
    pval_handling INTEGER NOT NULL DEFAULT 0,
    unit TEXT,
    decimals INTEGER NOT NULL DEFAULT 0,
    cycle_seconds INTEGER,
    interval_comp_mode INTEGER,
    daily_comp_mode INTEGER,
    monthly_comp_mode INTEGER,
    yearly_comp_mode INTEGER,
    comment TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_acron_identification ON acron_configs(identification);

CREATE TABLE IF NOT EXISTS provider_configs (
    datapoint_id INTEGER PRIMARY KEY REFERENCES datapoints(id) ON DELETE CASCADE,
    include_export INTEGER NOT NULL DEFAULT 0 CHECK(include_export IN (0, 1)),
    var_name TEXT,
    description TEXT,
    group_path TEXT,
    comment TEXT,
    dbp_type INTEGER NOT NULL DEFAULT 4,
    cycle_interval INTEGER,
    delta1_active INTEGER NOT NULL DEFAULT 0 CHECK(delta1_active IN (0, 1)),
    delta1_change TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS export_templates (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('acron', 'provider')),
    delimiter TEXT NOT NULL DEFAULT 'tab',
    encoding TEXT NOT NULL DEFAULT 'utf-16le-bom',
    UNIQUE(project_id, kind)
);

CREATE TABLE IF NOT EXISTS export_template_fields (
    template_id INTEGER NOT NULL REFERENCES export_templates(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    field_name TEXT NOT NULL,
    default_value TEXT,
    PRIMARY KEY(template_id, ordinal),
    UNIQUE(template_id, field_name)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    datapoint_id INTEGER REFERENCES datapoints(id) ON DELETE SET NULL,
    entity TEXT NOT NULL,
    field_name TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
