const SESSION_EDITED_KEY = "dpl-acron-edited-datapoints-v1";
const SESSION_IMPORT_KEY = "dpl-acron-edited-import-v1";

function loadSessionEditedIds() {
  try {
    const stored = JSON.parse(sessionStorage.getItem(SESSION_EDITED_KEY) || "[]");
    return new Set(Array.isArray(stored) ? stored.map(Number).filter(Number.isInteger) : []);
  } catch {
    return new Set();
  }
}

function loadSessionImportFingerprint() {
  try {
    return sessionStorage.getItem(SESSION_IMPORT_KEY) || "";
  } catch {
    return "";
  }
}

const state = {
  summary: null,
  rawItems: [],
  items: [],
  current: null,
  initialPayload: null,
  uploadFile: null,
  uploadFiles: [],
  pcs7Files: [],
  modules: [],
  searchTimer: null,
  saving: false,
  editedIds: loadSessionEditedIds(),
  importFingerprint: loadSessionImportFingerprint(),
  selectedIds: new Set(),
  undoStack: [],
  redoStack: [],
  historyBusy: false,
};

const $ = (selector) => document.querySelector(selector);
const text = (selector, next) => { $(selector).textContent = next ?? "–"; };
const value = (selector, next) => { $(selector).value = next ?? ""; };
const checked = (selector, next) => { $(selector).checked = Boolean(next); };

function updateSessionEditedSummary() {
  text("#stat-session-edited", state.editedIds.size);
  const chip = $("#session-edited-chip");
  chip.classList.toggle("has-edits", state.editedIds.size > 0);
}

function markSessionEdited(datapointId) {
  const id = Number(datapointId);
  if (!Number.isInteger(id)) return;
  state.editedIds.add(id);
  try {
    sessionStorage.setItem(SESSION_EDITED_KEY, JSON.stringify([...state.editedIds]));
  } catch {
    // Die optische Markierung funktioniert auch ohne verfügbaren Webspeicher bis zum Neuladen.
  }
  updateSessionEditedSummary();
}

function clearSessionEdited() {
  state.editedIds.clear();
  try {
    sessionStorage.removeItem(SESSION_EDITED_KEY);
  } catch {
    // Kein Abbruch, wenn der Browser Webspeicher blockiert.
  }
  updateSessionEditedSummary();
  state.selectedIds.clear();
}

function syncSessionEditedDataset(summary) {
  const fingerprint = summary.latestImport?.source_sha256
    || `${summary.project.id}:${summary.latestImport?.imported_at || "unknown"}`;
  if (state.importFingerprint !== fingerprint) {
    state.editedIds.clear();
    state.importFingerprint = fingerprint;
    try {
      sessionStorage.removeItem(SESSION_EDITED_KEY);
      sessionStorage.setItem(SESSION_IMPORT_KEY, fingerprint);
    } catch {
      // Ohne Webspeicher gilt die Markierung weiterhin für die aktuell geöffnete Seite.
    }
  }
  updateSessionEditedSummary();
}

const escapeHtml = (input) => String(input ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

async function fetchJson(url, options = {}) {
  const headers = new Headers(options.headers || {});
  const response = await fetch(url, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error || `HTTP ${response.status}`);
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function showToast(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.className = isError ? "visible error" : "visible";
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => { toast.className = ""; }, 3600);
}

function setExportAvailability(blocked) {
  document.querySelectorAll(".export-link").forEach((link) => {
    if (blocked) {
      link.setAttribute("aria-disabled", "true");
      link.setAttribute("tabindex", "-1");
      link.title = "Beheben Sie zuerst die Fehler in den Datensätzen.";
    } else {
      link.removeAttribute("aria-disabled");
      link.removeAttribute("tabindex");
      link.removeAttribute("title");
    }
  });
}

function renderSummary(summary) {
  state.summary = summary;
  const { project, counts, issues } = summary;
  const issueTotal = issues.error + issues.warning;
  const header = $("#header-status");
  const banner = $("#review-banner");
  const reviewStep = $("#step-review");
  const exportStep = $("#step-export");
  const readiness = $("#export-readiness");

  text("#project-name", project.name);
  text("#project-module", `${String(project.module_code || "pwk").toUpperCase()} · Modulversion ${project.module_version || "1.0.0"}`);
  const sourceName = summary.latestImport?.source_filename || "DPL";
  text("#project-meta", `Bauabschnitt ${project.ba_number} · ${counts.total} markierte Datensätze · Quelle: ${sourceName}`);
  text("#stat-total", counts.total);
  text("#stat-acron", counts.enabled);
  text("#stat-provider", counts.provider);
  text("#export-acron-count", counts.enabled);
  text("#export-provider-count", counts.provider);
  text("#acron-filename", project.acron_filename);
  text("#provider-filename", project.provider_filename);
  text("#guide-source-state", `${counts.total} Datensätze: ${counts.yellow || 0} gelb ausgewählt, ${counts.orange_changes || 0} mit orange geänderten Zellen.`);
  syncSessionEditedDataset(summary);
  renderPCS7Summary(summary.pcs7 || { active: false, imports: [], checked: 0, confirmed: 0, mismatches: 0 });

  header.className = "header-status";
  banner.className = "review-banner";
  reviewStep.className = "workflow-step";
  exportStep.className = "workflow-step";
  readiness.className = "export-readiness";

  if (issues.error) {
    header.classList.add("has-error");
    header.lastElementChild.textContent = `${issues.error} Fehler blockieren den Export`;
    banner.classList.add("is-error");
    text("#review-title", `${issues.error} Fehler müssen korrigiert werden`);
    text("#review-copy", `Zusätzlich gibt es ${issues.warning} fachliche Hinweise. Öffnen Sie die betroffenen Datensätze.`);
    text("#guide-review-state", `${issues.error} Fehler und ${issues.warning} Hinweise sind offen.`);
    text("#guide-export-state", "Der Download wird nach der Fehlerkorrektur freigegeben.");
    text("#export-readiness", `${issues.error} Fehler – Export gesperrt`);
    $("#review-action").hidden = false;
    reviewStep.classList.add("is-active");
    exportStep.classList.add("is-locked");
    readiness.classList.add("is-error");
    $(".review-icon").textContent = "!";
    setExportAvailability(true);
  } else if (issues.warning) {
    header.classList.add("has-warning");
    header.lastElementChild.textContent = `${issues.warning} Hinweise offen`;
    text("#review-title", `${issues.warning} fachliche Hinweise sollten geprüft werden`);
    text("#review-copy", "Der Export ist bereits möglich. Für einen verlässlichen Datenstand sollten Sie die Hinweise vorher bearbeiten.");
    text("#guide-review-state", `${issues.warning} Hinweise warten auf Ihre Prüfung.`);
    text("#guide-export-state", "Beide Dateien sind verfügbar; die Hinweise bleiben sichtbar.");
    text("#export-readiness", `Export möglich · ${issues.warning} Hinweise offen`);
    $("#review-action").hidden = false;
    reviewStep.classList.add("is-active");
    exportStep.classList.add("is-active");
    $(".review-icon").textContent = "!";
    setExportAvailability(false);
  } else {
    header.classList.add("is-ready");
    header.lastElementChild.textContent = "Alle Datensätze sind bereit";
    banner.classList.add("is-ready");
    text("#review-title", "Alle Datensätze sind für den Export bereit");
    text("#review-copy", `${counts.enabled} Acron- und ${counts.provider} Provider-Datensätze wurden geprüft.`);
    text("#guide-review-state", "Keine Fehler oder fachlichen Hinweise offen.");
    text("#guide-export-state", "Beide Exportdateien können jetzt heruntergeladen werden.");
    text("#export-readiness", "Bereit zum Herunterladen");
    $("#review-action").hidden = true;
    reviewStep.classList.add("is-complete");
    exportStep.classList.add("is-active");
    readiness.classList.add("is-ready");
    $(".review-icon").textContent = "✓";
    setExportAvailability(false);
  }

  $("#show-open-issues").textContent = issueTotal ? "Jetzt prüfen" : "Datensätze ansehen";

  const typeFilter = $("#type-filter");
  const current = typeFilter.value;
  typeFilter.innerHTML = '<option value="">Alle Wertetypen</option>' + summary.valueTypes
    .map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`).join("");
  typeFilter.value = current;
}

const auditFieldLabels = {
  enabled: "Exportstatus",
  identification: "Acron-Identifikation",
  name: "Acron-Anzeigename",
  group: "Gruppe",
  formula: "Formel / Prozessdatenverknüpfung",
  pvalFrom: "Messbereich von",
  pvalTo: "Messbereich bis",
  comment: "Kommentar",
  include: "Provider-Exportstatus",
  varName: "Provider-Variablenname",
  pcs7Reviewed: "Manuelle PCS7-Prüfung",
  import: "DPL-Import",
};

function auditValue(raw) {
  if (raw === null || raw === undefined) return "–";
  try {
    const parsed = JSON.parse(raw);
    if (parsed === null || parsed === "") return "–";
    return String(parsed);
  } catch {
    return String(raw);
  }
}

function formatAuditTime(raw) {
  if (!raw) return "";
  const date = new Date(`${String(raw).replace(" ", "T")}Z`);
  return Number.isNaN(date.getTime()) ? String(raw) : new Intl.DateTimeFormat("de-DE", {
    dateStyle: "short", timeStyle: "short",
  }).format(date);
}

function renderAudit(items) {
  const list = $("#activity-list");
  if (!items.length) {
    list.innerHTML = "<li>Noch keine manuellen Änderungen protokolliert.</li>";
    return;
  }
  list.innerHTML = items.map((entry) => {
    const target = entry.source_long_name || entry.identification || "Projekt";
    const label = auditFieldLabels[entry.field_name] || entry.field_name;
    const detail = entry.field_name === "import"
      ? `Neue Quelle: ${auditValue(entry.new_value)}`
      : `${auditValue(entry.old_value)} → ${auditValue(entry.new_value)}`;
    return `<li><strong>${escapeHtml(label)}</strong><span>${escapeHtml(target)} · ${detail} · ${escapeHtml(formatAuditTime(entry.changed_at))}</span></li>`;
  }).join("");
}

function statusLabel(level, count = 0) {
  if (level === "error") return `<span class="status error">Korrigieren${count > 1 ? ` (${count})` : ""}</span>`;
  if (level === "warning") return `<span class="status warning">Bitte prüfen${count > 1 ? ` (${count})` : ""}</span>`;
  if (level === "info") return '<span class="status info">Hinweis</span>';
  return '<span class="status">Bereit</span>';
}

function filteredAndSorted(items) {
  const selectedStatus = $("#status-filter").value;
  const filtered = items.filter((item) => {
    if (!selectedStatus) return true;
    if (selectedStatus === "attention") return ["error", "warning"].includes(item.issue_level);
    if (selectedStatus === "ready") return ["ok", "info"].includes(item.issue_level);
    if (selectedStatus === "pcs7") return item.pcs7?.status === "mismatch";
    if (selectedStatus === "pcs7_reviewed") return item.pcs7?.status === "reviewed";
    return item.issue_level === selectedStatus;
  });
  const priority = { error: 0, warning: 1, info: 2, ok: 3 };
  return filtered.sort((left, right) =>
    priority[left.issue_level] - priority[right.issue_level] || left.source_row - right.source_row
  );
}

function renderRows(items) {
  state.rawItems = items;
  state.items = filteredAndSorted([...items]);
  const body = $("#datapoint-rows");

  if (!state.items.length) {
    body.innerHTML = '<tr><td colspan="7" class="empty-state">Keine passenden Datensätze gefunden.<br>Setzen Sie die Filter zurück oder ändern Sie den Suchbegriff.</td></tr>';
  } else {
    body.innerHTML = state.items.map((item) => {
      const provider = Boolean(item.provider_include_export);
      const recentlyEdited = state.editedIds.has(Number(item.id));
      const issueCount = (item.issues || []).filter((issue) => issue.level === item.issue_level).length;
      const markerName = item.marker_color === "yellow" ? "Gelb ausgewählt" : "Orange markiert";
      const orangeChanged = Boolean(item.has_orange_changes);
      const pcs7Mismatch = item.pcs7?.status === "mismatch";
      const pcs7Reviewed = item.pcs7?.status === "reviewed";
      const rowClasses = [item.enabled ? "" : "disabled", recentlyEdited ? "recently-edited" : "", orangeChanged ? "orange-changed" : "", pcs7Mismatch ? "pcs7-mismatch" : "", pcs7Reviewed ? "pcs7-reviewed" : ""].filter(Boolean).join(" ");
      return `
        <tr data-id="${item.id}" data-revision="${item.revision}" class="${rowClasses}">
          <td data-label="Auswahl" class="selection-cell"><input class="row-select" type="checkbox" ${state.selectedIds.has(Number(item.id)) ? "checked" : ""} aria-label="${escapeHtml(item.source_long_name)} auswählen"></td>
          <td data-label="Prüfstatus">
            ${statusLabel(item.issue_level, issueCount)}
            ${pcs7Mismatch ? '<span class="pcs7-mismatch-badge">PCS7-Abweichung</span>' : ""}
            ${pcs7Reviewed ? '<span class="pcs7-reviewed-badge">PCS7 manuell geprüft</span>' : ""}
            ${recentlyEdited ? '<span class="session-edited-badge">Kürzlich bearbeitet</span>' : ""}
          </td>
          <td data-label="DPL-Datensatz">
            <span class="cell-title">${escapeHtml(item.source_number)} · ${escapeHtml(item.source_long_name)}</span>
            <span class="cell-subtitle">${escapeHtml(item.folder_level1 || "Ohne Ordner")} · <span class="cell-id">${escapeHtml(item.acron_identification)}</span></span>
          </td>
          <td data-label="Wertetyp">
            <div class="type-cell"><i class="marker ${escapeHtml(item.marker_color)}" title="${markerName}"></i><span class="tag">${escapeHtml(item.source_value_type)}</span>${orangeChanged ? '<span class="orange-change-badge">Orange Zelle geändert</span>' : ""}</div>
          </td>
          <td data-label="Exportziel">
            <span class="destination"><strong>${provider ? "Acron + Provider" : "Nur Acron"}</strong>${escapeHtml(item.source_pcs7_variable || "Acron-interner Wert")}</span>
          </td>
          <td data-label="Aktiv">
            <label class="row-toggle"><input class="row-enabled" type="checkbox" ${item.enabled ? "checked" : ""} aria-label="${escapeHtml(item.source_long_name)} im Acron-Export aktiv"> Ja</label>
          </td>
          <td data-label="Aktion">
            <div class="row-actions">
              ${(pcs7Mismatch || pcs7Reviewed) ? `<button class="row-review-button${pcs7Reviewed ? " is-reviewed" : ""}" type="button">${pcs7Reviewed ? "Prüfung zurücknehmen" : "Als geprüft"}</button>` : ""}
              <button class="row-button" type="button">${["error", "warning"].includes(item.issue_level) ? "Details prüfen" : "Bearbeiten"}</button>
            </div>
          </td>
        </tr>`;
    }).join("");
  }

  const suffix = state.items.length === items.length ? "Datensätze" : `von ${items.length} Datensätzen`;
  text("#result-count", `${state.items.length} ${suffix} angezeigt`);
  updateBulkToolbar();

  body.querySelectorAll("tr[data-id]").forEach((row) => {
    row.querySelector(".row-select").addEventListener("change", (event) => {
      const id = Number(row.dataset.id);
      if (event.target.checked) state.selectedIds.add(id); else state.selectedIds.delete(id);
      updateBulkToolbar();
    });
    row.querySelector(".row-button").addEventListener("click", () => openEditor(Number(row.dataset.id)));
    const reviewButton = row.querySelector(".row-review-button");
    if (reviewButton) {
      reviewButton.addEventListener("click", async () => {
        const item = state.rawItems.find((candidate) => Number(candidate.id) === Number(row.dataset.id));
        if (!item) return;
        const reviewed = item.pcs7?.status === "reviewed";
        try { await performReview([Number(row.dataset.id)], !reviewed); }
        catch (error) { showToast(error.message, true); }
      });
    }
    row.querySelector(".row-enabled").addEventListener("change", async (event) => {
      try {
        event.target.disabled = true;
        const before = state.rawItems.find((item) => Number(item.id) === Number(row.dataset.id));
        const updated = await fetchJson(`/api/datapoints/${row.dataset.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: event.target.checked, revision: Number(row.dataset.revision) }),
        });
        markSessionEdited(updated.id);
        if (before) rememberDatapointAction(
          before,
          updated,
          event.target.checked ? "Datensatz aktiviert" : "Datensatz deaktiviert",
          "enabled",
        );
        showToast(event.target.checked ? "Datensatz für Acron aktiviert." : "Datensatz vom Acron-Export ausgeschlossen.");
        await reload();
      } catch (error) {
        event.target.checked = !event.target.checked;
        event.target.disabled = false;
        showToast(error.status === 409 ? "Der Datensatz wurde bereits geändert. Die Liste wurde aktualisiert." : error.message, true);
        await loadItems().catch(() => {});
      }
    });
  });
}

function updateBulkToolbar() {
  const selected = state.items.filter((item) => state.selectedIds.has(Number(item.id)));
  $("#bulk-toolbar").hidden = !state.selectedIds.size;
  text("#bulk-count", state.selectedIds.size);
  $("#bulk-review").disabled = !selected.some((item) => item.pcs7?.status === "mismatch");
  $("#bulk-unreview").disabled = !selected.some((item) => item.pcs7?.status === "reviewed");
  const allVisible = state.items.length > 0 && state.items.every((item) => state.selectedIds.has(Number(item.id)));
  const someVisible = state.items.some((item) => state.selectedIds.has(Number(item.id)));
  $("#select-all-visible").checked = allVisible;
  $("#select-all-visible").indeterminate = someVisible && !allVisible;
}

function updateHistoryOverlay() {
  const editorOpen = Boolean($("#editor-dialog")?.open);
  $("#undo-action").disabled = state.historyBusy || editorOpen || !state.undoStack.length;
  $("#redo-action").disabled = state.historyBusy || editorOpen || !state.redoStack.length;
  const latest = state.undoStack.at(-1);
  text("#history-label", latest ? latest.label : "Keine Änderung");
}

function recordPayload(record) {
  return {
    enabled: Boolean(record.enabled),
    acron: {
      identification: record.acron_identification,
      name: record.acron_name,
      group: record.acron_group,
      type: Number(record.acron_type),
      formatOut: Number(record.acron_format_out),
      formula: record.acron_formula,
      pvalFrom: record.acron_pval_from,
      pvalTo: record.acron_pval_to,
      unit: record.acron_unit,
      decimals: Number(record.acron_decimals),
      pvalHandling: Number(record.acron_pval_handling),
      cycleSeconds: record.acron_cycle_seconds,
      intervalCompMode: record.acron_interval_comp_mode,
      dailyCompMode: record.acron_daily_comp_mode,
      monthlyCompMode: record.acron_monthly_comp_mode,
      yearlyCompMode: record.acron_yearly_comp_mode,
      comment: record.acron_comment,
    },
    provider: {
      include: Boolean(record.provider_include_export),
      varName: record.provider_var_name,
      description: record.provider_description,
      group: record.provider_group,
      dbpType: Number(record.provider_dbp_type),
      cycleInterval: record.provider_cycle_interval,
      delta1Active: Boolean(record.provider_delta1_active),
      delta1Change: record.provider_delta1_change,
      comment: record.provider_comment,
    },
  };
}

function rememberDatapointAction(before, after, label = "Datensatz bearbeitet", scope = "full") {
  state.undoStack.push({
    kind: "datapoint",
    id: Number(after.id),
    before: scope === "enabled" ? { enabled: Boolean(before.enabled) } : recordPayload(before),
    after: scope === "enabled" ? { enabled: Boolean(after.enabled) } : recordPayload(after),
    revision: Number(after.revision),
    label,
  });
  if (state.undoStack.length > 30) state.undoStack.shift();
  state.redoStack = [];
  updateHistoryOverlay();
}

function rememberReviewAction(ids, before, after, items) {
  state.undoStack.push({
    kind: "review", ids, before, after,
    revisions: Object.fromEntries(items.map((item) => [item.id, item.revision])),
    label: `${ids.length} ${ids.length === 1 ? "Datensatz" : "Datensätze"} ${after ? "geprüft" : "wieder geöffnet"}`,
  });
  if (state.undoStack.length > 30) state.undoStack.shift();
  state.redoStack = [];
  updateHistoryOverlay();
}

async function requestBulkReview(ids, reviewed, revisions = null) {
  const source = new Map(state.rawItems.map((item) => [Number(item.id), item]));
  const entries = ids.map((id) => ({
    id,
    revision: revisions ? Number(revisions[id]) : Number(source.get(Number(id))?.revision),
  }));
  return fetchJson("/api/datapoints/bulk-review", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items: entries, reviewed }),
  });
}

async function performReview(selectedIds, reviewed) {
  const eligible = state.rawItems.filter((item) => selectedIds.includes(Number(item.id)) && (
    reviewed ? item.pcs7?.status === "mismatch" : item.pcs7?.status === "reviewed"
  ));
  if (!eligible.length) return;
  const ids = eligible.map((item) => Number(item.id));
  const response = await requestBulkReview(ids, reviewed);
  response.items.forEach((item) => markSessionEdited(item.id));
  rememberReviewAction(ids, !reviewed, reviewed, response.items);
  ids.forEach((id) => state.selectedIds.delete(id));
  await reload();
  showToast(`${ids.length} ${ids.length === 1 ? "Datensatz wurde" : "Datensätze wurden"} ${reviewed ? "als geprüft markiert" : "wieder geöffnet"}.`);
}

async function replayHistory(direction) {
  const sourceStack = direction === "undo" ? state.undoStack : state.redoStack;
  const targetStack = direction === "undo" ? state.redoStack : state.undoStack;
  const action = sourceStack.at(-1);
  if (!action || state.historyBusy) return;
  state.historyBusy = true;
  updateHistoryOverlay();
  try {
    if (action.kind === "review") {
      const reviewed = direction === "undo" ? action.before : action.after;
      const response = await requestBulkReview(action.ids, reviewed, action.revisions);
      action.revisions = Object.fromEntries(response.items.map((item) => [item.id, item.revision]));
      response.items.forEach((item) => markSessionEdited(item.id));
    } else {
      const payload = direction === "undo" ? action.before : action.after;
      const updated = await fetchJson(`/api/datapoints/${action.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...payload, revision: action.revision }),
      });
      action.revision = Number(updated.revision);
      markSessionEdited(updated.id);
    }
    sourceStack.pop();
    targetStack.push(action);
    await reload();
    showToast(direction === "undo" ? "Letzte Änderung rückgängig gemacht." : "Änderung wiederhergestellt.");
  } catch (error) {
    showToast(error.status === 409 ? "Rückgängig nicht möglich: Mindestens ein Datensatz wurde zwischenzeitlich geändert." : error.message, true);
    await reload().catch(() => {});
  } finally {
    state.historyBusy = false;
    updateHistoryOverlay();
  }
}

function isNativeUndoTarget(target) {
  return target instanceof Element && Boolean(target.closest(
    "input:not([type='checkbox']):not([type='radio']), textarea, select, [contenteditable='true']"
  ));
}

function handleHistoryShortcut(event) {
  if (!event.ctrlKey || event.altKey || event.metaKey || isNativeUndoTarget(event.target)) return;
  const key = event.key.toLocaleLowerCase("de");
  const direction = key === "z" && !event.shiftKey ? "undo"
    : (key === "y" || (key === "z" && event.shiftKey)) ? "redo" : null;
  if (!direction) return;
  const available = direction === "undo" ? state.undoStack.length : state.redoStack.length;
  if (!available || state.historyBusy || $("#editor-dialog").open) return;
  event.preventDefault();
  replayHistory(direction);
}

function itemQuery() {
  const query = new URLSearchParams();
  if ($("#search").value) query.set("search", $("#search").value);
  if ($("#type-filter").value) query.set("type", $("#type-filter").value);
  if ($("#marker-filter").value) query.set("marker", $("#marker-filter").value);
  return query;
}

async function loadItems() {
  const payload = await fetchJson(`/api/datapoints?${itemQuery()}`);
  renderRows(payload.items);
}

function renderModules(modules) {
  state.modules = modules;
  const select = $("#dpl-module-select");
  const preferred = select.value || state.summary?.project?.module_code || modules[0]?.code || "";
  select.innerHTML = modules.map((item) =>
    `<option value="${escapeHtml(item.code)}">${escapeHtml(item.selectLabel || item.name)}</option>`
  ).join("");
  if (modules.some((item) => item.code === preferred)) select.value = preferred;
  select.disabled = modules.length === 0;
  updateModuleSelectionContext();
}

function updateModuleSelectionContext() {
  const select = $("#dpl-module-select");
  const selected = state.modules.find((item) => item.code === select.value);
  select.dataset.module = selected?.code || "";
  if (!selected) {
    text("#dpl-module-help", "Es ist kein Anlagenmodul verfügbar.");
    return;
  }
  const types = Array.isArray(selected.supportedTypes) ? selected.supportedTypes.join(", ") : "";
  const layoutInfo = Number(selected.layoutCount) > 1 ? ` ${selected.layoutCount} DPL-Layouts werden automatisch erkannt.` : "";
  text("#dpl-module-help", `${selected.description}${types ? ` Unterstützte Anlagentypen: ${types}.` : ""}${layoutInfo}`);
}

async function reload() {
  const [summary, payload, audit, modules] = await Promise.all([
    fetchJson("/api/project"),
    fetchJson(`/api/datapoints?${itemQuery()}`),
    fetchJson("/api/audit?limit=12"),
    fetchJson("/api/modules"),
  ]);
  renderSummary(summary);
  renderRows(payload.items);
  renderAudit(audit.items);
  renderModules(modules.items || []);
}

const aggregationOptions = [
  ["", "Nicht verdichten"], [7, "Arithmetischer Mittelwert"], [10, "Letzter Wert"],
  [12, "Summe"], [24, "Gewichteter Mittelwert"], [25, "Erster Wert"], [28, "Verketten"],
];

function prepareAggregationSelects() {
  document.querySelectorAll(".aggregation-select").forEach((select) => {
    select.innerHTML = aggregationOptions.map(([code, label]) => `<option value="${code}">${label}</option>`).join("");
  });
}

const issueFields = {
  missing_acron_identification: "#acron-identification",
  missing_acron_name: "#acron-name",
  missing_acron_group: "#acron-group",
  duplicate_identification: "#acron-identification",
  online_formula_missing: "#acron-formula",
  calculation_formula_missing: "#acron-formula",
  provider_var_missing: "#provider-var-name",
  provider_group_missing: "#provider-group",
  range_invalid: ".field-group",
  range_order: ".field-group",
  pcs7_acron_reference_mismatch: "#acron-formula",
  pcs7_provider_reference_mismatch: "#provider-var-name",
};

function renderPCS7Summary(pcs7) {
  const imports = pcs7.imports || [];
  const list = $("#pcs7-imports");
  list.innerHTML = imports.length
    ? imports.map((item) => `<div class="pcs7-import-card"><span>${escapeHtml(item.kind_label)}</span><strong>${escapeHtml(item.source_filename)}</strong><small>${item.reference_count} Referenzen</small></div>`).join("")
    : '<p class="pcs7-empty">Keine PCS7-Dateien hinterlegt. Die DPL kann weiterhin ohne diese optionale Prüfung exportiert werden.</p>';
  if (pcs7.active) {
    text("#pcs7-summary-text", `${pcs7.confirmed} automatisch bestätigt · ${pcs7.reviewed || 0} manuell geprüft · ${pcs7.mismatches} offen`);
    text("#pcs7-summary-state", pcs7.mismatches ? `${pcs7.mismatches} Abweichungen` : "Prüfung aktiv");
    $("#pcs7-summary-state").className = pcs7.mismatches ? "pcs7-summary-state has-error" : "pcs7-summary-state is-ready";
  } else if (imports.length) {
    text("#pcs7-summary-text", "Zusatzexporte erkannt. Für die vollständige Negativprüfung fehlt noch der Parameterexport.");
    text("#pcs7-summary-state", "Parameter fehlt");
    $("#pcs7-summary-state").className = "pcs7-summary-state has-warning";
  } else {
    text("#pcs7-summary-text", "Noch keine PCS7-Exporte hochgeladen.");
    text("#pcs7-summary-state", "Optional");
    $("#pcs7-summary-state").className = "pcs7-summary-state";
  }
}

function renderEditorIssues(issues) {
  const panel = $("#editor-issues");
  panel.className = "issue-panel";
  document.querySelectorAll(".field-has-issue, .field-has-error").forEach((element) => {
    element.classList.remove("field-has-issue", "field-has-error");
  });

  const actionable = issues.filter((issue) => ["error", "warning"].includes(issue.level));
  if (!actionable.length) {
    panel.classList.add("is-ready");
    panel.innerHTML = "<strong>Dieser Datensatz ist bereit.</strong><p>Es gibt keine Pflichtkorrekturen. Technische Details können Sie bei Bedarf öffnen.</p>";
  } else {
    if (actionable.some((issue) => issue.level === "error")) panel.classList.add("is-error");
    panel.innerHTML = `<strong>${actionable.length === 1 ? "Bitte prüfen Sie diesen Punkt:" : `Bitte prüfen Sie diese ${actionable.length} Punkte:`}</strong><ul>${actionable.map((issue) => `<li>${escapeHtml(issue.message)}</li>`).join("")}</ul>`;
  }

  issues.forEach((issue) => {
    const selector = issueFields[issue.code];
    if (!selector) return;
    const field = $(selector);
    const container = field.matches(".field-group") ? field : field.closest("label");
    if (!container) return;
    container.classList.add("field-has-issue");
    if (issue.level === "error") container.classList.add("field-has-error");
  });
}

function renderEditorConflict(current) {
  const panel = $("#editor-issues");
  panel.className = "issue-panel is-conflict";
  panel.innerHTML = `
    <strong>Dieser Datensatz wurde zwischenzeitlich von einer anderen Person geändert.</strong>
    <p>Ihre Eingaben wurden nicht überschrieben und auch nicht gespeichert. Laden Sie den aktuellen Stand, bevor Sie erneut bearbeiten.</p>
    <button class="button button-secondary conflict-reload" type="button">Aktuellen Stand laden</button>`;
  panel.querySelector(".conflict-reload").addEventListener("click", () => {
    populateEditor(current);
    showToast("Der aktuelle Datenstand wurde geladen. Ihre bisherigen Eingaben wurden verworfen.");
  });
}

function updateProviderState() {
  const enabled = $("#provider-include").checked;
  $("#provider-fields").disabled = !enabled;
  text("#provider-summary-status", enabled ? "Im Export enthalten" : "Nicht enthalten");
}

function populateEditor(item) {
  state.current = item;
  state.initialPayload = null;
  text("#editor-title", item.source_long_name);
  text("#editor-subtitle", `DPL-Nr. ${item.source_number} · ${item.marker_color === "yellow" ? "gelb ausgewählt" : "orange markiert"}${item.has_orange_changes ? " · orange Zelle geändert" : ""} · ${item.source_value_type}`);
  text("#source-long", item.source_long_name);
  text("#source-short", item.source_vg_short);
  text("#source-pcs7", item.source_pcs7_variable || "Acron-interner Wert");
  text("#source-unit", `${item.source_unit || "–"} / ${item.source_decimals ?? "0"}`);
  text("#source-position", `${item.source_sheet || "PWK_Analogwerte"}, Zeile ${item.source_row}`);

  value("#acron-identification", item.acron_identification);
  value("#acron-name", item.acron_name);
  value("#acron-group", item.acron_group);
  value("#acron-type", item.acron_type);
  value("#acron-format-out", item.acron_format_out);
  value("#acron-formula", item.acron_formula);
  value("#acron-pval-from", item.acron_pval_from);
  value("#acron-pval-to", item.acron_pval_to);
  value("#acron-unit", item.acron_unit);
  value("#acron-decimals", item.acron_decimals);
  value("#acron-pval-handling", item.acron_pval_handling);
  value("#acron-cycle", item.acron_cycle_seconds);
  value("#acron-interval-mode", item.acron_interval_comp_mode);
  value("#acron-daily-mode", item.acron_daily_comp_mode);
  value("#acron-monthly-mode", item.acron_monthly_comp_mode);
  value("#acron-yearly-mode", item.acron_yearly_comp_mode);
  value("#acron-comment", item.acron_comment);

  checked("#provider-include", item.provider_include_export);
  value("#provider-var-name", item.provider_var_name);
  value("#provider-description", item.provider_description);
  value("#provider-group", item.provider_group);
  value("#provider-dbp-type", item.provider_dbp_type);
  value("#provider-cycle", item.provider_cycle_interval);
  checked("#provider-delta-active", item.provider_delta1_active);
  value("#provider-delta-change", item.provider_delta1_change);
  value("#provider-comment", item.provider_comment);
  checked("#datapoint-enabled", item.enabled);

  $("#acron-advanced").open = false;
  $("#provider-panel").open = (item.issues || []).some((issue) => issue.code.startsWith("provider_"));
  updateProviderState();
  renderEditorIssues(item.issues || []);
  renderPCS7Review(item);

  const editorStatus = $("#editor-status");
  const level = (item.issues || []).some((issue) => issue.level === "error") ? "error"
    : (item.issues || []).some((issue) => issue.level === "warning") ? "warning"
      : (item.issues || []).some((issue) => issue.level === "info") ? "info" : "ok";
  editorStatus.className = `status ${level === "ok" ? "" : level}`;
  editorStatus.textContent = level === "error" ? "Korrektur nötig" : level === "warning" ? "Bitte prüfen" : level === "info" ? "Hinweis" : "Bereit";

  state.initialPayload = JSON.stringify(editorPayload());
  updateDirtyState();
  updateHistoryOverlay();
}

async function openEditor(id) {
  try {
    const item = await fetchJson(`/api/datapoints/${id}`);
    populateEditor(item);
    $("#editor-dialog").showModal();
    updateHistoryOverlay();
    const firstIssue = (item.issues || []).find((issue) => issueFields[issue.code] && ["error", "warning"].includes(issue.level));
    if (firstIssue) {
      window.requestAnimationFrame(() => {
        const field = $(issueFields[firstIssue.code]);
        if (field && !field.matches(".field-group")) field.focus();
      });
    }
  } catch (error) {
    showToast(error.message, true);
  }
}

const optionalInteger = (selector) => $(selector).value === "" ? null : Number($(selector).value);

function editorPayload() {
  return {
    revision: state.current?.revision,
    enabled: $("#datapoint-enabled").checked,
    acron: {
      identification: $("#acron-identification").value,
      name: $("#acron-name").value,
      group: $("#acron-group").value,
      type: Number($("#acron-type").value),
      formatOut: Number($("#acron-format-out").value),
      formula: $("#acron-formula").value,
      pvalFrom: $("#acron-pval-from").value,
      pvalTo: $("#acron-pval-to").value,
      unit: $("#acron-unit").value,
      decimals: Number($("#acron-decimals").value),
      pvalHandling: Number($("#acron-pval-handling").value),
      cycleSeconds: optionalInteger("#acron-cycle"),
      intervalCompMode: optionalInteger("#acron-interval-mode"),
      dailyCompMode: optionalInteger("#acron-daily-mode"),
      monthlyCompMode: optionalInteger("#acron-monthly-mode"),
      yearlyCompMode: optionalInteger("#acron-yearly-mode"),
      comment: $("#acron-comment").value,
    },
    provider: {
      include: $("#provider-include").checked,
      varName: $("#provider-var-name").value,
      description: $("#provider-description").value,
      group: $("#provider-group").value,
      dbpType: Number($("#provider-dbp-type").value),
      cycleInterval: optionalInteger("#provider-cycle"),
      delta1Active: $("#provider-delta-active").checked,
      delta1Change: $("#provider-delta-change").value,
      comment: $("#provider-comment").value,
    },
  };
}

function isEditorDirty() {
  return Boolean(state.current && state.initialPayload && JSON.stringify(editorPayload()) !== state.initialPayload);
}

function updateDirtyState() {
  const dirty = isEditorDirty();
  text("#unsaved-state", dirty ? "Ungespeicherte Änderungen" : "");
  $("#save-editor").disabled = !dirty || state.saving;
  if (!$("#pcs7-review-control").hidden) $("#pcs7-review-button").disabled = dirty || state.saving;
}

function renderPCS7Review(item) {
  const panel = $("#pcs7-review-control");
  const status = item.pcs7?.status;
  panel.hidden = !["mismatch", "reviewed"].includes(status);
  if (panel.hidden) return;
  const reviewed = status === "reviewed";
  text("#pcs7-review-title", reviewed ? "PCS7-Abweichung wurde manuell geprüft" : "PCS7-Abweichung manuell bewerten");
  const messages = item.pcs7?.messages || (item.issues || [])
    .filter((issue) => issue.code.startsWith("pcs7_"))
    .map((issue) => issue.message.replace(/ Manuell geprüft\.$/, ""));
  text("#pcs7-review-detail", messages.join(" ") || "Die Referenz weicht vom PCS7-Export ab.");
  $("#pcs7-review-button").textContent = reviewed ? "Prüfung zurücknehmen" : "Als geprüft markieren";
  $("#pcs7-review-button").className = reviewed ? "button button-secondary is-reviewed" : "button button-secondary";
}

async function togglePCS7Review() {
  if (!state.current || isEditorDirty()) return;
  const reviewed = state.current.pcs7?.status === "reviewed";
  const button = $("#pcs7-review-button");
  button.disabled = true;
  try {
    const updated = await fetchJson(`/api/datapoints/${state.current.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ revision: state.current.revision, pcs7Reviewed: !reviewed }),
    });
    markSessionEdited(updated.id);
    rememberReviewAction([Number(updated.id)], reviewed, !reviewed, [updated]);
    populateEditor(updated);
    await reload();
    showToast(reviewed ? "Manuelle PCS7-Prüfung zurückgenommen." : "PCS7-Abweichung als manuell geprüft markiert.");
  } catch (error) {
    showToast(error.status === 409 ? "Der Datensatz wurde zwischenzeitlich geändert." : error.message, true);
    if (error.status === 409) await reload().catch(() => {});
  } finally {
    button.disabled = false;
  }
}

function closeEditor() {
  if (isEditorDirty() && !window.confirm("Ungespeicherte Änderungen verwerfen?")) return;
  state.current = null;
  state.initialPayload = null;
  $("#editor-dialog").close();
  updateHistoryOverlay();
}

async function saveEditor(event) {
  event.preventDefault();
  const button = $("#save-editor");
  state.saving = true;
  button.disabled = true;
  button.textContent = "Wird gespeichert …";

  try {
    const before = state.current;
    const updated = await fetchJson(`/api/datapoints/${state.current.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(editorPayload()),
    });
    markSessionEdited(updated.id);
    rememberDatapointAction(before, updated, `„${updated.source_long_name}“ bearbeitet`);
    const stillOpen = (updated.issues || []).some((issue) => ["error", "warning"].includes(issue.level));
    if (stillOpen) {
      populateEditor(updated);
      showToast("Gespeichert. Bitte prüfen Sie die verbleibenden Hinweise.");
      await reload();
    } else {
      state.initialPayload = JSON.stringify(editorPayload());
      state.current = null;
      $("#editor-dialog").close();
      updateHistoryOverlay();
      showToast("Gespeichert und geprüft.");
      await reload();
    }
  } catch (error) {
    if (error.status === 409 && error.payload.current) {
      renderEditorConflict(error.payload.current);
      showToast("Speichern gestoppt: Eine andere Person war schneller.", true);
    } else {
      showToast(error.message, true);
    }
  } finally {
    state.saving = false;
    button.textContent = "Speichern und prüfen";
    if ($("#editor-dialog").open) updateDirtyState();
  }
}

async function focusAttention() {
  $("#search").value = "";
  $("#type-filter").value = "";
  $("#marker-filter").value = "";
  $("#status-filter").value = state.summary && (state.summary.issues.error || state.summary.issues.warning) ? "attention" : "";
  try {
    await loadItems();
    $("#datapoint-workspace").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    showToast(error.message, true);
  }
}

function resetFilters() {
  $("#search").value = "";
  $("#status-filter").value = "";
  $("#type-filter").value = "";
  $("#marker-filter").value = "";
  loadItems().catch((error) => showToast(error.message, true));
}

function formatFileSize(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1).replace(".", ",")} MB`;
}

function showUploadResult(message, isError = false) {
  const result = $("#upload-result");
  result.hidden = false;
  result.className = isError ? "upload-result is-error" : "upload-result";
  result.textContent = message;
}

function uploadFileKey(file) {
  return `${file.name}\u0000${file.size}\u0000${file.lastModified || 0}`;
}

function renderUploadQueue() {
  const container = $("#upload-selection");
  const list = $("#upload-file-list");
  if (!state.uploadFiles.length) {
    state.uploadFile = null;
    container.hidden = true;
    list.innerHTML = "";
    return;
  }
  if (!state.uploadFiles.includes(state.uploadFile)) state.uploadFile = state.uploadFiles[0];
  text("#upload-filename", state.uploadFile.name);
  text("#upload-filesize", `${formatFileSize(state.uploadFile.size)} · XLSX · bereit zur Prüfung`);
  text("#upload-count-badge", `${state.uploadFiles.length} ${state.uploadFiles.length === 1 ? "Datei" : "Dateien"}`);
  text("#upload-start-filename", state.uploadFile.name);
  list.innerHTML = state.uploadFiles.map((file, index) => `
    <div class="upload-file-row${file === state.uploadFile ? " is-selected" : ""}" ${file === state.uploadFile ? 'aria-current="true"' : ""}>
      <label>
        <input type="radio" name="active-upload-file" value="${index}" ${file === state.uploadFile ? "checked" : ""}>
        <span class="upload-file-order" aria-hidden="true">${index + 1}</span>
        <span class="upload-file-copy"><strong>${escapeHtml(file.name)}</strong><small>${formatFileSize(file.size)} · XLSX-Datei</small></span>
        <span class="upload-file-state">${file === state.uploadFile ? "Ausgewählt" : "Auswählen"}</span>
      </label>
      <button type="button" data-remove-upload="${index}" aria-label="${escapeHtml(file.name)} aus der Liste entfernen" title="Aus Liste entfernen">×</button>
    </div>`).join("");
  list.querySelectorAll('input[name="active-upload-file"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      state.uploadFile = state.uploadFiles[Number(radio.value)];
      renderUploadQueue();
    });
  });
  list.querySelectorAll("[data-remove-upload]").forEach((button) => {
    button.addEventListener("click", () => {
      const removed = state.uploadFiles.splice(Number(button.dataset.removeUpload), 1)[0];
      if (removed === state.uploadFile) state.uploadFile = state.uploadFiles[0] || null;
      renderUploadQueue();
    });
  });
  container.hidden = false;
}

function selectUploadFiles(fileList) {
  $("#upload-result").hidden = true;
  const files = [...(fileList || [])];
  if (!files.length) return;
  const valid = files.filter((file) => file.name.toLocaleLowerCase("de").endsWith(".xlsx") && file.size <= 25 * 1024 * 1024);
  const known = new Set(state.uploadFiles.map(uploadFileKey));
  const additions = valid.filter((file) => {
    const key = uploadFileKey(file);
    if (known.has(key)) return false;
    known.add(key);
    return true;
  });
  state.uploadFiles.push(...additions);
  if (!state.uploadFile && additions.length) state.uploadFile = additions[0];
  renderUploadQueue();
  const rejected = files.length - valid.length;
  if (rejected) {
    showUploadResult(`${rejected} Datei(en) wurden übersprungen. Zulässig sind ausschließlich XLSX-Dateien bis 25 MB.`, true);
  } else if (!additions.length) {
    showUploadResult("Diese Datei(en) befinden sich bereits in der Upload-Liste.", true);
  }
}

function pasteUploadFile(event) {
  const files = [...(event.clipboardData?.files || [])];
  if (!files.length) return;
  event.preventDefault();
  const before = state.uploadFiles.length;
  selectUploadFiles(files);
  const added = state.uploadFiles.length - before;
  if (added) {
    showToast(`${added} DPL-Datei(en) wurden aus der Zwischenablage übernommen.`);
    $("#upload-selection").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

async function uploadDpl() {
  const file = state.uploadFile;
  if (!file) return;
  const moduleCode = $("#dpl-module-select").value;
  const selectedModule = state.modules.find((item) => item.code === moduleCode);
  if (!selectedModule) {
    showUploadResult("Bitte wählen Sie zuerst einen Anlagentyp aus.", true);
    return;
  }
  const confirmed = window.confirm(
    `„${file.name}“ als ${selectedModule.name}-DPL übernehmen?\n\nDie Übernahme erfolgt transaktional. Geöffnete ältere Datensätze müssen danach neu geladen werden.`
  );
  if (!confirmed) return;

  const button = $("#upload-start");
  button.disabled = true;
  $("#upload-selection").hidden = true;
  $("#upload-progress").hidden = false;
  $("#dpl-drop-zone").classList.add("is-disabled");

  try {
    const result = await fetchJson("/api/import-dpl", {
      method: "POST",
      headers: {
        "Content-Type": file.type || "application/octet-stream",
        "X-Filename": encodeURIComponent(file.name),
        "X-Anlage-Module": moduleCode,
      },
      body: file,
    });
    state.uploadFiles = state.uploadFiles.filter((item) => item !== file);
    state.uploadFile = state.uploadFiles[0] || null;
    $("#dpl-upload-input").value = "";
    $("#search").value = "";
    $("#status-filter").value = "";
    $("#type-filter").value = "";
    $("#marker-filter").value = "";
    clearSessionEdited();
    await reload();
    renderUploadQueue();
    const backupNote = result.backend === "mariadb"
      ? " Der neue Datenstand wurde vollständig und transaktional in MariaDB aktiviert."
      : "";
    showUploadResult(
      `Import erfolgreich (${result.module_name || selectedModule.name}): ${result.selected} markierte Datensätze, davon ${result.provider} für den Provider-Export.${backupNote}`
    );
    $("#project-name").scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (error) {
    $("#upload-selection").hidden = false;
    showUploadResult(`Die DPL wurde nicht übernommen: ${error.message}`, true);
  } finally {
    button.disabled = false;
    $("#upload-progress").hidden = true;
    $("#dpl-drop-zone").classList.remove("is-disabled");
  }
}

function showPCS7Result(message, isError = false) {
  const result = $("#pcs7-result");
  result.hidden = false;
  result.className = isError ? "pcs7-result is-error" : "pcs7-result";
  result.textContent = message;
}

function selectPCS7Files(fileList) {
  $("#pcs7-result").hidden = true;
  const files = [...(fileList || [])];
  const invalid = files.find((file) => !file.name.toLocaleLowerCase("de").endsWith(".xlsx") || file.size > 25 * 1024 * 1024);
  if (invalid) {
    state.pcs7Files = [];
    $("#pcs7-selection").hidden = true;
    showPCS7Result(`„${invalid.name}“ ist keine gültige XLSX-Datei bis 25 MB.`, true);
    return;
  }
  state.pcs7Files = files;
  $("#pcs7-selection").hidden = !files.length;
  $("#pcs7-selected-files").innerHTML = files.map((file) => `
    <div><strong>${escapeHtml(file.name)}</strong><span>${formatFileSize(file.size)}</span></div>`).join("");
}

async function uploadPCS7Files() {
  if (!state.pcs7Files.length) return;
  const files = [...state.pcs7Files];
  const button = $("#pcs7-upload-start");
  button.disabled = true;
  $("#pcs7-selection").hidden = true;
  $("#pcs7-upload-progress").hidden = false;
  $("#pcs7-drop-zone").classList.add("is-disabled");
  const imported = [];
  try {
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      text("#pcs7-progress-text", `${index + 1} von ${files.length}: ${file.name}`);
      const result = await fetchJson("/api/import-pcs7", {
        method: "POST",
        headers: {
          "Content-Type": file.type || "application/octet-stream",
          "X-Filename": encodeURIComponent(file.name),
        },
        body: file,
      });
      imported.push(`${result.kind_label}: ${result.reference_count} Referenzen`);
    }
    state.pcs7Files = [];
    $("#pcs7-upload-input").value = "";
    await reload();
    showPCS7Result(`PCS7-Prüfung aktualisiert: ${imported.join(" · ")}. Unstimmigkeiten sind in der Datensatzliste markiert.`);
  } catch (error) {
    $("#pcs7-selection").hidden = false;
    showPCS7Result(`PCS7-Datei konnte nicht übernommen werden: ${error.message}`, true);
    await reload().catch(() => {});
  } finally {
    button.disabled = false;
    $("#pcs7-upload-progress").hidden = true;
    $("#pcs7-drop-zone").classList.remove("is-disabled");
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  prepareAggregationSelects();

  $("#editor-form").addEventListener("submit", saveEditor);
  $("#editor-form").addEventListener("input", updateDirtyState);
  $("#editor-form").addEventListener("change", updateDirtyState);
  $("#close-editor").addEventListener("click", closeEditor);
  $("#cancel-editor").addEventListener("click", closeEditor);
  $("#editor-dialog").addEventListener("cancel", (event) => {
    event.preventDefault();
    closeEditor();
  });
  $("#provider-include").addEventListener("change", () => {
    updateProviderState();
    updateDirtyState();
  });
  $("#pcs7-review-button").addEventListener("click", togglePCS7Review);

  $("#search").addEventListener("input", () => {
    window.clearTimeout(state.searchTimer);
    state.searchTimer = window.setTimeout(() => loadItems().catch((error) => showToast(error.message, true)), 220);
  });
  $("#status-filter").addEventListener("change", () => renderRows(state.rawItems));
  $("#type-filter").addEventListener("change", () => loadItems().catch((error) => showToast(error.message, true)));
  $("#marker-filter").addEventListener("change", () => loadItems().catch((error) => showToast(error.message, true)));
  $("#reset-filters").addEventListener("click", resetFilters);
  $("#show-open-issues").addEventListener("click", focusAttention);
  $("#review-action").addEventListener("click", focusAttention);
  $("#select-all-visible").addEventListener("change", (event) => {
    state.items.forEach((item) => {
      const id = Number(item.id);
      if (event.target.checked) state.selectedIds.add(id); else state.selectedIds.delete(id);
    });
    renderRows(state.rawItems);
  });
  $("#bulk-clear").addEventListener("click", () => {
    state.selectedIds.clear();
    renderRows(state.rawItems);
  });
  $("#bulk-review").addEventListener("click", () => performReview([...state.selectedIds], true).catch((error) => showToast(error.message, true)));
  $("#bulk-unreview").addEventListener("click", () => performReview([...state.selectedIds], false).catch((error) => showToast(error.message, true)));
  $("#undo-action").addEventListener("click", () => replayHistory("undo"));
  $("#redo-action").addEventListener("click", () => replayHistory("redo"));
  document.addEventListener("keydown", handleHistoryShortcut);
  updateHistoryOverlay();

  $("#dpl-upload-input").addEventListener("change", (event) => selectUploadFiles(event.target.files));
  $("#dpl-module-select").addEventListener("change", updateModuleSelectionContext);
  document.addEventListener("paste", pasteUploadFile);
  $("#upload-start").addEventListener("click", uploadDpl);
  $("#dpl-drop-zone").addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      $("#dpl-upload-input").click();
    }
  });
  ["dragenter", "dragover"].forEach((name) => {
    $("#dpl-drop-zone").addEventListener(name, (event) => {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      $("#dpl-drop-zone").classList.add("is-dragging");
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    $("#dpl-drop-zone").addEventListener(name, (event) => {
      event.preventDefault();
      $("#dpl-drop-zone").classList.remove("is-dragging");
    });
  });
  $("#dpl-drop-zone").addEventListener("drop", (event) => {
    selectUploadFiles(event.dataTransfer.files);
  });

  $("#pcs7-upload-input").addEventListener("change", (event) => selectPCS7Files(event.target.files));
  $("#pcs7-upload-start").addEventListener("click", uploadPCS7Files);
  $("#pcs7-drop-zone").addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      $("#pcs7-upload-input").click();
    }
  });
  ["dragenter", "dragover"].forEach((name) => {
    $("#pcs7-drop-zone").addEventListener(name, (event) => {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      $("#pcs7-drop-zone").classList.add("is-dragging");
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    $("#pcs7-drop-zone").addEventListener(name, (event) => {
      event.preventDefault();
      $("#pcs7-drop-zone").classList.remove("is-dragging");
    });
  });
  $("#pcs7-drop-zone").addEventListener("drop", (event) => selectPCS7Files(event.dataTransfer.files));

  document.querySelectorAll(".export-link").forEach((link) => {
    link.addEventListener("click", (event) => {
      if (link.getAttribute("aria-disabled") === "true") {
        event.preventDefault();
        showToast("Der Export ist gesperrt. Beheben Sie zuerst die Fehler.", true);
        focusAttention();
        return;
      }
      showToast("Die aktuelle Exportdatei wird erstellt.");
    });
  });

  try {
    await reload();
  } catch (error) {
    $("#header-status").lastElementChild.textContent = "Daten konnten nicht geladen werden";
    showToast(error.message, true);
  }
});
