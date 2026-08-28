"use strict";

const params = new URLSearchParams(window.location.search);
const token = params.get("token") || "";
const identityFields = new Set([
  "raw_code", "functional_code", "economic_code", "name", "institution",
  "form", "subdocument", "section", "fact_kind"
]);

const state = {
  workspace: null,
  documents: [],
  document: null,
  page: null,
  rows: [],
  columns: [],
  zoom: 100,
  sheetStart: 1,
  filter: "all",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

async function api(path, options = {}) {
  const separator = path.includes("?") ? "&" : "?";
  const response = await fetch(`${path}${separator}token=${encodeURIComponent(token)}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Annotation-Token": token,
      ...(options.headers || {}),
    },
  });
  const payload = await response.json().catch(() => ({error: `HTTP ${response.status}`}));
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"
  })[char]);
}

function setError(message = "") {
  const box = $("#error-box");
  box.textContent = message;
  box.classList.toggle("hidden", !message);
}

function setSaveState(message) {
  $("#save-state").textContent = message;
}

function classifiedCount(doc) {
  return doc.classified || 0;
}

function renderProgress() {
  const total = state.workspace.source_units || 0;
  const classified = state.documents.reduce((sum, doc) => sum + classifiedCount(doc), 0);
  const pct = total ? Math.round(100 * classified / total) : 0;
  $("#progress-label").textContent = `${classified.toLocaleString("ro-RO")} / ${total.toLocaleString("ro-RO")} unități clasificate (${pct}%)`;
  $("#progress-bar").style.width = `${pct}%`;
}

function documentVisible(doc) {
  const query = $("#document-search").value.trim().toLocaleLowerCase("ro");
  if (query && !doc.municipality.toLocaleLowerCase("ro").includes(query)) return false;
  if (state.filter === "full" && doc.benchmark_scope !== "full") return false;
  if (state.filter === "unfinished" && doc.classified >= doc.source_units) return false;
  return true;
}

function renderDocumentList() {
  const list = $("#document-list");
  list.innerHTML = "";
  for (const doc of state.documents.filter(documentVisible)) {
    const button = document.createElement("button");
    button.className = `document-item${state.document?.id === doc.id ? " active" : ""}`;
    const done = doc.classified >= doc.source_units;
    const dotClass = done ? "done" : (doc.benchmark_scope === "full" ? "full" : "");
    const rate = doc.observed_strict_line_rate == null ? "-" : `${doc.observed_strict_line_rate}% strict`;
    button.innerHTML = `<strong><span class="dot ${dotClass}"></span>${escapeHtml(doc.municipality)}</strong><small><span>${rate}</span><span>${doc.classified}/${doc.source_units}</span></small>`;
    button.addEventListener("click", () => selectDocument(doc.id));
    list.appendChild(button);
  }
}

function firstUnreviewedPage(doc) {
  return doc.first_unreviewed || 1;
}

async function selectDocument(id, page = null) {
  const doc = state.documents.find((item) => item.id === id);
  if (!doc) return;
  state.document = doc;
  renderDocumentList();
  $("#document-title").textContent = doc.municipality;
  $("#document-meta").textContent = `${doc.county_name} · SIRUTA ${doc.siruta} · ${doc.source_format.toUpperCase()} · ${doc.source_units} unități`;
  $("#benchmark-scope").value = doc.benchmark_scope;
  $("#page-number").max = doc.source_units;
  $("#page-total").textContent = `/ ${doc.source_units}`;
  await loadPage(page || firstUnreviewedPage(doc));
}

function blankRow() {
  return {
    id: crypto.randomUUID(), raw_code: "", functional_code: "", economic_code: "",
    name: "", institution: $("#default-institution").value || "",
    form: $("#default-form").value || "", subdocument: $("#default-subdocument").value || "",
    section: $("#default-section").value || "", fact_kind: "budget", values: {}, note: ""
  };
}

function rowInput(row, field, className = "") {
  return `<input class="${className}" data-field="${field}" value="${escapeHtml(row[field] || "")}">`;
}

function renderTruthTable() {
  const head = $("#truth-table thead");
  const body = $("#truth-table tbody");
  head.innerHTML = `<tr><th>Cod tipărit</th><th>Cod func.</th><th>Cod econ.</th><th>Denumire</th><th>Tip</th>${state.columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("")}<th></th></tr>`;
  body.innerHTML = "";
  state.rows.forEach((row, index) => {
    const tr = document.createElement("tr");
    tr.dataset.index = index;
    const values = state.columns.map((column) => {
      const cell = row.values?.[column] || {printed: "", certain: true};
      return `<td class="value-cell"><div class="value-wrap"><input data-value="${escapeHtml(column)}" value="${escapeHtml(cell.printed || "")}" placeholder="valoare"><label title="Citire sigură"><input type="checkbox" data-certain="${escapeHtml(column)}" ${cell.certain !== false ? "checked" : ""}>sigur</label></div></td>`;
    }).join("");
    tr.innerHTML = `<td>${rowInput(row, "raw_code")}</td><td>${rowInput(row, "functional_code")}</td><td>${rowInput(row, "economic_code")}</td><td>${rowInput(row, "name", "name-input")}</td><td><select data-field="fact_kind"><option value="budget" ${row.fact_kind !== "annex" ? "selected" : ""}>buget</option><option value="annex" ${row.fact_kind === "annex" ? "selected" : ""}>anexă</option></select></td>${values}<td><div class="row-tools"><button data-context title="Context">ctx</button><button data-copy title="Duplică">+</button><button data-delete title="Șterge">×</button></div></td>`;
    body.appendChild(tr);
    const context = document.createElement("tr");
    context.className = "context-row hidden-row";
    context.dataset.contextIndex = index;
    context.innerHTML = `<td colspan="${6 + state.columns.length}"><div class="row-context-grid"><label>Instituție${rowInput(row, "institution")}</label><label>Formular${rowInput(row, "form")}</label><label>Subdocument${rowInput(row, "subdocument")}</label><label>Secțiune${rowInput(row, "section")}</label></div></td>`;
    body.appendChild(context);
  });
  bindRowEvents();
}

function syncRowsFromTable() {
  $$("#truth-table tbody tr[data-index]").forEach((tr) => {
    const index = Number(tr.dataset.index);
    const row = state.rows[index];
    tr.querySelectorAll("[data-field]").forEach((input) => { row[input.dataset.field] = input.value; });
    tr.querySelectorAll("[data-value]").forEach((input) => {
      const column = input.dataset.value;
      const certain = tr.querySelector(`[data-certain="${CSS.escape(column)}"]`)?.checked ?? true;
      if (input.value.trim()) row.values[column] = {printed: input.value.trim(), certain};
      else delete row.values[column];
    });
    const context = $(`#truth-table tbody tr[data-context-index="${index}"]`);
    context?.querySelectorAll("[data-field]").forEach((input) => { row[input.dataset.field] = input.value; });
  });
}

function bindRowEvents() {
  $$("#truth-table [data-context]").forEach((button) => button.addEventListener("click", (event) => {
    event.preventDefault();
    const index = button.closest("tr").dataset.index;
    $(`#truth-table tr[data-context-index="${index}"]`).classList.toggle("hidden-row");
  }));
  $$("#truth-table [data-copy]").forEach((button) => button.addEventListener("click", (event) => {
    event.preventDefault(); syncRowsFromTable();
    const index = Number(button.closest("tr").dataset.index);
    const copy = structuredClone(state.rows[index]); copy.id = crypto.randomUUID();
    state.rows.splice(index + 1, 0, copy); renderTruthTable();
  }));
  $$("#truth-table [data-delete]").forEach((button) => button.addEventListener("click", (event) => {
    event.preventDefault(); syncRowsFromTable();
    state.rows.splice(Number(button.closest("tr").dataset.index), 1); renderTruthTable();
  }));
}

function setEditorLocked(locked) {
  $$(".editor-panel input, .editor-panel select, .editor-panel textarea, #truth-editor button").forEach((control) => { control.disabled = locked; });
  $("#unfreeze-truth").classList.toggle("hidden", !locked);
  $("#unfreeze-truth").disabled = false;
  $("#save-draft").classList.toggle("hidden", locked);
  $("#freeze-truth").classList.toggle("hidden", locked);
  $("#benchmark-scope").disabled = false;
}

function renderReview(payload) {
  const review = payload.page.review;
  state.rows = structuredClone(review.rows || []);
  state.columns = [...(review.columns || [])];
  $("#page-kind").value = review.page_kind;
  $("#source-unit").value = review.source_unit;
  $("#number-notation").value = review.number_notation;
  $("#reviewer").value = review.reviewer || localStorage.getItem("bgc-annotator") || "";
  $("#default-institution").value = review.default_institution || "";
  $("#default-form").value = review.default_form || "";
  $("#default-subdocument").value = review.default_subdocument || "";
  $("#default-section").value = review.default_section || "";
  $("#numeric-columns").value = state.columns.join(", ");
  $("#no-numeric-cells").checked = review.no_numeric_cells;
  $("#exhaustive").checked = review.exhaustive || (payload.document.benchmark_scope === "full" && review.status === "unreviewed");
  $("#review-note").value = review.note || "";
  const badge = $("#review-badge");
  badge.textContent = review.status.replace("_", " ");
  badge.className = `badge ${review.status}`;
  renderTruthTable();
  setEditorLocked(review.status === "frozen");
  const suggestion = payload.page.machine_suggestion;
  const suggestionBox = $("#machine-suggestion");
  suggestionBox.classList.toggle("hidden", !suggestion);
  if (suggestion) suggestionBox.textContent = `Sugestie deblocată după clasificare: ${suggestion.suggested_kind} · ${suggestion.reason}${suggestion.layout ? ` · ${suggestion.layout}` : ""}`;
  renderComparison(payload.page.comparison);
}

function renderComparison(comparison) {
  const panel = $("#comparison-panel");
  panel.classList.toggle("hidden", !comparison);
  if (!comparison) return;
  const metric = (value, label) => `<div class="metric"><strong>${value ?? "-"}</strong><span>${label}</span></div>`;
  $("#comparison-metrics").innerHTML = metric(comparison.recall_pct == null ? "-" : `${comparison.recall_pct}%`, "recall celule") + metric(comparison.precision_pct == null ? "-" : `${comparison.precision_pct}%`, "precizie celule") + metric(`${comparison.matched}/${comparison.expected}`, "celule regăsite");
  $("#comparison-misses").innerHTML = comparison.misses.length ? `<ul class="difference-list">${comparison.misses.map((item) => `<li>${escapeHtml(item.identity)} · ${escapeHtml(item.column)} = ${escapeHtml(item.expected_mii_lei)} mii lei</li>`).join("")}</ul>` : "<p>Nicio celulă lipsă.</p>";
  $("#comparison-extras").innerHTML = comparison.extras.length ? `<ul class="difference-list">${comparison.extras.slice(0, 200).map((item) => `<li>${escapeHtml(item.raw_code || item.name)} · ${escapeHtml(item.column)} = ${escapeHtml(item.value_mii_lei)} mii lei</li>`).join("")}</ul>` : "<p>Nicio celulă suplimentară.</p>";
  const review = state.page.page.review;
  $("#second-review-status").textContent = review.second_reviewed_at ? `Confirmat de ${review.second_reviewer}` : (comparison.misses.length || comparison.extras.length ? "Necesar pentru discrepanțe" : "Opțional");
  $("#second-reviewer").disabled = Boolean(review.second_reviewed_at);
  $("#complete-second-review").disabled = Boolean(review.second_reviewed_at);
}

async function renderSource(payload) {
  const view = $("#source-view");
  $("#source-title").textContent = payload.page.label;
  if (payload.page.source_type === "pdf_page") {
    view.classList.remove("sheet-mode");
    $("#sheet-pagination").classList.add("hidden");
    view.innerHTML = `<img alt="${escapeHtml(payload.page.label)}" src="/api/render?document=${encodeURIComponent(payload.document.id)}&page=${payload.page.number}&token=${encodeURIComponent(token)}">`;
    applyZoom();
  } else {
    view.classList.add("sheet-mode");
    $("#sheet-pagination").classList.remove("hidden");
    await loadSheetWindow();
  }
}

async function loadSheetWindow() {
  const payload = await api(`/api/sheet?document=${encodeURIComponent(state.document.id)}&page=${state.page.page.number}&start=${state.sheetStart}`);
  const width = Math.max(...payload.rows.map((row) => row.length), 0);
  const rows = payload.rows.map((row, offset) => `<tr><th>${payload.start_row + offset}</th>${Array.from({length: width}, (_, index) => `<td>${escapeHtml(row[index] || "")}</td>`).join("")}</tr>`).join("");
  $("#source-view").innerHTML = `<table class="sheet-preview"><tbody>${rows}</tbody></table>`;
  $("#sheet-range").textContent = `Rânduri ${payload.start_row}-${payload.end_row} din ${payload.total_rows}`;
  $("#sheet-previous").disabled = payload.start_row <= 1;
  $("#sheet-next").disabled = payload.end_row >= payload.total_rows;
}

function applyZoom() {
  const image = $("#source-view img");
  if (image) image.style.width = `${state.zoom}%`;
  $("#zoom-label").textContent = `${state.zoom}%`;
}

async function loadPage(number) {
  if (!state.document) return;
  const page = Math.max(1, Math.min(state.document.source_units, Number(number) || 1));
  setError(); setSaveState("Se încarcă..."); state.sheetStart = 1;
  try {
    const payload = await api(`/api/page?document=${encodeURIComponent(state.document.id)}&page=${page}`);
    state.page = payload;
    $("#page-number").value = page;
    renderReview(payload);
    await renderSource(payload);
    setSaveState("Încărcat");
  } catch (error) { setError(error.message); setSaveState("Eroare"); }
}

function reviewPayload() {
  syncRowsFromTable();
  const reviewer = $("#reviewer").value.trim();
  if (reviewer) localStorage.setItem("bgc-annotator", reviewer);
  return {
    expected_revision: state.page.page.review.revision,
    page_kind: $("#page-kind").value,
    source_unit: $("#source-unit").value,
    number_notation: $("#number-notation").value,
    exhaustive: $("#exhaustive").checked,
    columns: state.columns,
    rows: state.rows.filter((row) => Object.keys(row.values || {}).length),
    reviewer,
    no_numeric_cells: $("#no-numeric-cells").checked,
    note: $("#review-note").value.trim() || null,
    default_institution: $("#default-institution").value.trim() || null,
    default_form: $("#default-form").value.trim() || null,
    default_subdocument: $("#default-subdocument").value.trim() || null,
    default_section: $("#default-section").value.trim() || null,
  };
}

async function savePage(action) {
  setError(); setSaveState(action === "freeze" ? "Se îngheață..." : "Se salvează...");
  try {
    await api("/api/page", {method: "POST", body: JSON.stringify({
      document: state.document.id, page: state.page.page.number,
      action, review: reviewPayload()
    })});
    await reloadWorkspace(false);
    await loadPage(state.page.page.number);
    setSaveState(action === "freeze" ? "Adevăr înghețat" : "Salvat");
  } catch (error) { setError(error.message); setSaveState("Nesalvat"); }
}

async function unfreezePage() {
  if (!confirm("Deblocarea marchează pagina pentru o nouă revizie. Continui?")) return;
  try {
    await api("/api/page", {method: "POST", body: JSON.stringify({
      document: state.document.id, page: state.page.page.number, action: "unfreeze",
      review: {expected_revision: state.page.page.review.revision}
    })});
    await reloadWorkspace(false); await loadPage(state.page.page.number);
  } catch (error) { setError(error.message); }
}

function applyColumns() {
  syncRowsFromTable();
  const columns = $("#numeric-columns").value.split(",").map((item) => item.trim()).filter(Boolean);
  state.columns = [...new Set(columns)];
  for (const row of state.rows) {
    for (const column of Object.keys(row.values || {})) if (!state.columns.includes(column)) delete row.values[column];
  }
  renderTruthTable();
}

function importTsv(event) {
  event.preventDefault();
  const box = $("#paste-error"); box.classList.add("hidden");
  try {
    const lines = $("#tsv-input").value.trim().split(/\r?\n/).map((line) => line.split("\t"));
    if (lines.length < 2) throw new Error("Lipește un antet și cel puțin un rând.");
    const headers = lines[0].map((header) => header.trim());
    const numeric = headers.filter((header) => !identityFields.has(header));
    if (!numeric.length) throw new Error("Antetul nu conține coloane numerice.");
    state.columns = [...new Set([...state.columns, ...numeric])];
    for (const cells of lines.slice(1)) {
      const row = blankRow();
      headers.forEach((header, index) => {
        const value = (cells[index] || "").trim();
        if (!value) return;
        if (identityFields.has(header)) row[header] = value;
        else row.values[header] = {printed: value, certain: true};
      });
      if (Object.keys(row.values).length) state.rows.push(row);
    }
    $("#numeric-columns").value = state.columns.join(", ");
    renderTruthTable(); $("#paste-dialog").close(); $("#tsv-input").value = "";
  } catch (error) { box.textContent = error.message; box.classList.remove("hidden"); }
}

async function nextUnreviewed() {
  if (state.page.page.next_unreviewed) {
    await loadPage(state.page.page.next_unreviewed); return;
  }
  const startDoc = state.documents.findIndex((doc) => doc.id === state.document.id);
  for (let offset = 1; offset <= state.documents.length; offset++) {
    const doc = state.documents[(startDoc + offset) % state.documents.length];
    if (doc.first_unreviewed) {
      await selectDocument(doc.id, doc.first_unreviewed); return;
    }
  }
  setSaveState("Inventarul este complet clasificat");
}

async function reloadWorkspace(selectFirst = true) {
  state.workspace = await api("/api/workspace");
  state.documents = state.workspace.documents;
  renderProgress(); renderDocumentList();
  if (selectFirst && state.documents.length) await selectDocument(state.documents[0].id);
}

async function completeSecondReview() {
  const reviewer = $("#second-reviewer").value.trim();
  try {
    await api("/api/second-review", {method: "POST", body: JSON.stringify({
      document: state.document.id, page: state.page.page.number,
      expected_revision: state.page.page.review.revision, reviewer
    })});
    await reloadWorkspace(false); await loadPage(state.page.page.number);
  } catch (error) { setError(error.message); }
}

function bindEvents() {
  $("#document-search").addEventListener("input", renderDocumentList);
  $$(".filter").forEach((button) => button.addEventListener("click", () => {
    $$(".filter").forEach((item) => item.classList.remove("active")); button.classList.add("active");
    state.filter = button.dataset.filter; renderDocumentList();
  }));
  $("#previous-page").addEventListener("click", () => loadPage(state.page.page.number - 1));
  $("#next-page").addEventListener("click", () => loadPage(state.page.page.number + 1));
  $("#page-number").addEventListener("change", (event) => loadPage(event.target.value));
  $("#next-unreviewed").addEventListener("click", nextUnreviewed);
  $("#zoom-out").addEventListener("click", () => { state.zoom = Math.max(40, state.zoom - 20); applyZoom(); });
  $("#zoom-in").addEventListener("click", () => { state.zoom = Math.min(240, state.zoom + 20); applyZoom(); });
  $("#sheet-previous").addEventListener("click", () => { state.sheetStart = Math.max(1, state.sheetStart - 150); loadSheetWindow(); });
  $("#sheet-next").addEventListener("click", () => { state.sheetStart += 150; loadSheetWindow(); });
  $("#apply-columns").addEventListener("click", applyColumns);
  $("#add-row").addEventListener("click", () => { syncRowsFromTable(); state.rows.push(blankRow()); renderTruthTable(); });
  $("#paste-tsv").addEventListener("click", () => $("#paste-dialog").showModal());
  $("#import-tsv").addEventListener("click", importTsv);
  $("#save-draft").addEventListener("click", () => savePage("save"));
  $("#freeze-truth").addEventListener("click", () => savePage("freeze"));
  $("#unfreeze-truth").addEventListener("click", unfreezePage);
  $("#complete-second-review").addEventListener("click", completeSecondReview);
  $("#benchmark-scope").addEventListener("change", async (event) => {
    try {
      await api("/api/scope", {method: "POST", body: JSON.stringify({document: state.document.id, scope: event.target.value})});
      await reloadWorkspace(false); state.document = state.documents.find((doc) => doc.id === state.document.id); renderDocumentList();
    } catch (error) { setError(error.message); }
  });
}

bindEvents();
reloadWorkspace(true).catch((error) => setError(error.message));
