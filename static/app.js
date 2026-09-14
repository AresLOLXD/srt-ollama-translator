const configStatus = document.getElementById("config-status");
const uploadStatus = document.getElementById("upload-status");
const jobsTableBody = document.getElementById("jobs-table-body");
const modelSelect = document.getElementById("model-select");
const ollamaUrlInput = document.getElementById("ollama-url-input");
const ollamaTimeoutInput = document.getElementById("ollama-timeout-input");

async function loadConfig() {
  try {
    const response = await fetch("/api/config");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    ollamaUrlInput.value = data.ollama_base_url;
    ollamaTimeoutInput.value = data.ollama_timeout;
  } catch (err) {
    ollamaUrlInput.value = "";
    ollamaUrlInput.placeholder = "No se pudo cargar la configuración";
    if (configStatus) configStatus.textContent = "Error: No se pudo cargar la configuración";
  }
}

async function saveConfig() {
  configStatus.textContent = "Guardando...";
  const response = await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ollama_base_url: ollamaUrlInput.value,
      ollama_timeout: Number(ollamaTimeoutInput.value),
    }),
  });
  configStatus.textContent = response.ok ? "Guardado" : "Error al guardar";
}

async function loadModels() {
  modelSelect.innerHTML = "";
  try {
    const response = await fetch("/api/models");
    if (!response.ok) throw new Error("No se pudo conectar a Ollama");
    const data = await response.json();
    for (const model of data.models) {
      const option = document.createElement("option");
      option.value = model;
      option.textContent = model;
      modelSelect.appendChild(option);
    }
  } catch (err) {
    const option = document.createElement("option");
    option.textContent = "No se pudieron cargar los modelos";
    modelSelect.appendChild(option);
  }
}

async function uploadJob(event) {
  event.preventDefault();
  const zipInput = document.getElementById("zip-input");
  const sourceLangSelect = document.getElementById("source-lang-select");

  const formData = new FormData();
  formData.append("file", zipInput.files[0]);
  formData.append("model", modelSelect.value);
  formData.append("source_lang", sourceLangSelect.value);

  uploadStatus.textContent = "Subiendo...";
  const response = await fetch("/api/jobs", { method: "POST", body: formData });
  if (response.ok) {
    uploadStatus.textContent = "Trabajo encolado";
    zipInput.value = "";
    await refreshJobs();
  } else {
    const error = await response.json();
    uploadStatus.textContent = `Error: ${error.detail}`;
  }
}

function renderJobRow(job) {
  const row = document.createElement("tr");

  const nameCell = document.createElement("td");
  nameCell.textContent = job.original_zip_name;
  row.appendChild(nameCell);

  const statusCell = document.createElement("td");
  statusCell.textContent = job.status;
  row.appendChild(statusCell);

  const progressCell = document.createElement("td");
  const percent = job.total_files > 0 ? Math.round((job.processed_files / job.total_files) * 100) : 0;

  const barTrack = document.createElement("div");
  barTrack.className = "progress-bar-track";
  const barFill = document.createElement("div");
  barFill.className = "progress-bar-fill";
  barFill.style.width = `${percent}%`;
  barTrack.appendChild(barFill);
  progressCell.appendChild(barTrack);

  const filesLine = document.createElement("div");
  filesLine.className = "progress-files";
  filesLine.textContent = `${job.processed_files} / ${job.total_files} archivos (${percent}%)`;
  progressCell.appendChild(filesLine);

  if (job.current_file) {
    const detailLine = document.createElement("div");
    detailLine.className = "progress-detail";
    detailLine.textContent = `Procesando: ${job.current_file.filename} (${job.current_file.translated_blocks}/${job.current_file.total_blocks} bloques)`;
    progressCell.appendChild(detailLine);
  }

  row.appendChild(progressCell);

  const actionCell = document.createElement("td");
  if (job.status === "completed" || job.status === "completed_with_errors") {
    const link = document.createElement("a");
    link.href = `/api/jobs/${job.id}/download`;
    link.textContent = "Descargar";
    actionCell.appendChild(link);
  }

  const deleteButton = document.createElement("button");
  deleteButton.textContent = "Borrar";
  deleteButton.disabled = job.status === "processing";
  deleteButton.addEventListener("click", async () => {
    try {
      const response = await fetch(`/api/jobs/${job.id}`, { method: "DELETE" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      await refreshJobs();
    } catch (err) {
      alert("Error al borrar el trabajo");
    }
  });
  actionCell.appendChild(deleteButton);

  row.appendChild(actionCell);

  return row;
}

async function refreshJobs() {
  try {
    const response = await fetch("/api/jobs");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (!Array.isArray(data.jobs)) throw new Error("Invalid response format");
    jobsTableBody.innerHTML = "";
    for (const job of data.jobs) {
      jobsTableBody.appendChild(renderJobRow(job));
    }
  } catch (err) {
    // Silent fail: do not update table, let polling retry in next cycle
  }
}

document.getElementById("save-config-btn").addEventListener("click", saveConfig);
document.getElementById("refresh-models-btn").addEventListener("click", loadModels);
document.getElementById("upload-form").addEventListener("submit", uploadJob);

loadConfig();
loadModels();
refreshJobs();
setInterval(refreshJobs, 3000);
