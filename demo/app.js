import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v0.29.5/full/pyodide.mjs";

const $ = (id) => document.getElementById(id);
let pyodide;
let currentText = "";
let userFileSelected = false;
let result = null;
let currentTab = "reject";

const setStatus = (status) => { $("status").textContent = status; };
const setValue = (id, value) => { $(id).textContent = String(value); };

function presentTable(rows, keys, errorColumn) {
  const container = $("table-container");
  container.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No records in this category.";
    container.append(empty);
    return;
  }
  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const header = document.createElement("tr");
  for (const key of keys) {
    const th = document.createElement("th"); th.textContent = key; header.append(th);
  }
  thead.append(header); table.append(thead);
  const tbody = document.createElement("tbody");
  for (const row of rows.slice(0, 250)) {
    const tr = document.createElement("tr");
    for (const key of keys) {
      const cell = document.createElement("td");
      cell.textContent = row[key] == null ? "—" : String(row[key]);
      if (key === errorColumn) cell.className = "error-cell";
      cell.title = cell.textContent;
      tr.append(cell);
    }
    tbody.append(tr);
  }
  table.append(tbody); container.append(table);
}

function clearResult(message = "Load a sample or select your own CSV to see the data quality report.") {
  result = null;
  for (const id of ["rows-read", "rows-valid", "rows-rejected", "rows-loaded"]) setValue(id, "—");
  setValue("deduped", "Duplicates dropped: —");
  setValue("errors", "Errors: —");
  setValue("result-label", "READY TO TEST");
  setValue("row-caption", "Row-level errors include the field and reason.");
  $("download").disabled = true;
  const empty = document.createElement("div");
  empty.className = "empty";
  empty.textContent = message;
  $("table-container").replaceChildren(empty);
}

function failInput(message) {
  currentText = "";
  userFileSelected = false;
  setValue("filename", "No feed loaded");
  clearResult(message);
  setStatus(message);
}

function renderTab(tab) {
  currentTab = tab;
  for (const name of ["reject", "valid", "json"]) {
    $(name + "-tab").classList.toggle("active", name === tab);
    $(name + "-tab").setAttribute("aria-selected", name === tab ? "true" : "false");
  }
  if (!result) return;
  if (tab === "reject") {
    presentTable(result.rejected_rows, ["source_row_number", "field", "error_type", "error_message", "raw_value"], "error_message");
    setValue("row-caption", `${result.rejected_rows.length} detailed error record(s), one per error`);
  } else if (tab === "valid") {
    presentTable(result.valid_rows, Object.keys(result.valid_rows[0] || {}));
    setValue("row-caption", `${result.rows_valid} rows accepted by Pydantic`);
  } else {
    const pre = document.createElement("pre");
    const { valid_rows, rejected_rows, rejects_csv, ...summary } = result;
    pre.textContent = JSON.stringify(summary, null, 2);
    $("table-container").replaceChildren(pre);
    setValue("row-caption", "Machine-readable batch metrics");
  }
}

async function validate() {
  if (!pyodide || !currentText) return;
  clearResult("Validation is running…");
  setValue("result-label", "VALIDATING");
  $("validate").disabled = true;
  setStatus("Running Pydantic checks…");
  // Yield one paint frame before synchronous Python processing.
  await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve, 0)));
  try {
    pyodide.globals.set("csv_text", currentText);
    pyodide.globals.set("selected_model", $("model").value);
    const json = pyodide.runPython("from etlguard.browser_demo import validate_csv; validate_csv(csv_text, selected_model)");
    result = JSON.parse(json);
    setValue("rows-read", result.rows_read);
    setValue("rows-valid", result.rows_valid);
    setValue("rows-rejected", result.rows_rejected);
    setValue("rows-loaded", result.rows_loaded_sqlite);
    setValue("deduped", `Duplicates dropped: ${result.duplicates_dropped}`);
    setValue("errors", `Errors: ${result.rejected_rows.length}`);
    setValue("result-label", "VALIDATED LOCALLY");
    $("download").disabled = false;
    renderTab("reject");
    setStatus("Validation complete");
  } catch (error) {
    clearResult("Validation failed. Check the selected CSV and schema, then try again.");
    setValue("result-label", "VALIDATION ERROR");
    setStatus(`Validation error: ${String(error.message || error).slice(0, 150)}`);
  } finally {
    pyodide.globals.delete("csv_text");
    pyodide.globals.delete("selected_model");
    $("validate").disabled = false;
  }
}

async function sample() {
  const model = $("model").value.toLowerCase();
  try {
    const response = await fetch(`samples/${model}s_messy.csv`);
    if (!response.ok) throw new Error("Sample missing");
    currentText = await response.text();
    userFileSelected = false;
    setValue("filename", `${model}s_messy.csv (synthetic sample)`);
    await validate();
  } catch (error) { failInput(`Could not load sample: ${String(error.message || error)}`); }
}

async function loadFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".csv")) { failInput("Select a .csv file"); return; }
  if (file.size > 5_000_000) { failInput("Browser demo accepts CSV files up to 5 MB"); return; }
  setStatus("Reading CSV…");
  try {
    currentText = await file.text();
    if (!currentText.trim()) throw new Error("The selected CSV is empty");
    userFileSelected = true;
    setValue("filename", file.name);
    await validate();
  } catch (error) {
    failInput(`Could not read CSV: ${String(error.message || error)}`);
  }
}

$("dropzone").addEventListener("click", () => $("file").click());
$("dropzone").addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("file").click(); } });
$("file").addEventListener("change", (e) => loadFile(e.target.files[0]));
$("dropzone").addEventListener("dragover", (e) => { e.preventDefault(); $("dropzone").classList.add("dragging"); });
$("dropzone").addEventListener("dragleave", () => $("dropzone").classList.remove("dragging"));
$("dropzone").addEventListener("drop", (e) => { e.preventDefault(); $("dropzone").classList.remove("dragging"); loadFile(e.dataTransfer.files[0]); });
$("sample").addEventListener("click", sample);
$("validate").addEventListener("click", validate);
$("model").addEventListener("change", () => userFileSelected ? validate() : sample());
for (const tab of ["reject", "valid", "json"]) $(tab + "-tab").addEventListener("click", () => renderTab(tab));
$("download").addEventListener("click", () => {
  if (!result) return;
  const blob = new Blob(["\ufeff", result.rejects_csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url; anchor.download = `etlguard_${result.model.toLowerCase()}_rejects.csv`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

try {
  setStatus("Loading Pyodide runtime…");
  pyodide = await loadPyodide({ indexURL: "https://cdn.jsdelivr.net/pyodide/v0.29.5/full/" });
  setStatus("Loading Pydantic v2…");
  await pyodide.loadPackage("pydantic");
  const response = await fetch("etlguard_core.zip");
  if (!response.ok) throw new Error("Python core bundle unavailable");
  pyodide.FS.writeFile("/tmp/etlguard_core.zip", new Uint8Array(await response.arrayBuffer()));
  pyodide.runPython("import sys; sys.path.insert(0, '/tmp/etlguard_core.zip')");
  $("validate").disabled = false;
  setStatus("Python engine ready");
  await sample();
} catch (error) {
  setStatus(`Could not start browser Python: ${String(error).slice(0, 140)}`);
}
