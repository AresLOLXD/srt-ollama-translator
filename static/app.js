const configStatus = document.getElementById("config-status");
const uploadStatus = document.getElementById("upload-status");
const jobsTableBody = document.getElementById("jobs-table-body");
const modelSelect = document.getElementById("model-select");
const ollamaUrlInput = document.getElementById("ollama-url-input");

async function loadConfig() {
  const response = await fetch("/api/config");
  const data = await response.json();
  ollamaUrlInput.value = data.ollama_base_url;
}

async function saveConfig() {
  configStatus.textContent = "Guardando...";
  const response = await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ollama_base_url: ollamaUrlInput.value }),
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
  progressCell.textContent = `${job.processed_files} / ${job.total_files} archivos`;
  row.appendChild(progressCell);

  const actionCell = document.createElement("td");
  if (job.status === "completed" || job.status === "completed_with_errors") {
    const link = document.createElement("a");
    link.href = `/api/jobs/${job.id}/download`;
    link.textContent = "Descargar";
    actionCell.appendChild(link);
  }
  row.appendChild(actionCell);

  return row;
}

async function refreshJobs() {
  const response = await fetch("/api/jobs");
  const data = await response.json();
  jobsTableBody.innerHTML = "";
  for (const job of data.jobs) {
    jobsTableBody.appendChild(renderJobRow(job));
  }
}

document.getElementById("save-config-btn").addEventListener("click", saveConfig);
document.getElementById("refresh-models-btn").addEventListener("click", loadModels);
document.getElementById("upload-form").addEventListener("submit", uploadJob);

loadConfig();
loadModels();
refreshJobs();
setInterval(refreshJobs, 3000);
