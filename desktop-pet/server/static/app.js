const form = document.querySelector("#pet-form");
const submitButton = document.querySelector("#submit");
const progress = document.querySelector("#progress");
const statusText = document.querySelector("#status");
const percentText = document.querySelector("#percent");
const messageText = document.querySelector("#message");
const barFill = document.querySelector("#bar-fill");
const download = document.querySelector("#download");
const adminTokenInput = document.querySelector("#admin-token");
const aiSettingsForm = document.querySelector("#ai-settings-form");
const loadAiSettingsButton = document.querySelector("#load-ai-settings");
const aiSettingsStatus = document.querySelector("#ai-settings-status");

function adminHeaders() {
  const token = adminTokenInput.value.trim();
  if (!token) throw new Error("请先输入 ADMIN_TOKEN");
  return { "X-Admin-Token": token };
}

function setAiSettingsStatus(message, isError = false) {
  aiSettingsStatus.textContent = message;
  aiSettingsStatus.classList.toggle("error", isError);
}

function fillAiSettings(values) {
  document.querySelector("#ai-enabled").checked = Boolean(values.enabled);
  document.querySelector("#ai-base-url").value = values.api_base_url || "";
  document.querySelector("#ai-model").value = values.image_model || "";
  document.querySelector("#ai-frame-count").value = values.frame_count || 4;
  document.querySelector("#ai-max-references").value = values.max_references || 2;
  document.querySelector("#ai-timeout").value = values.timeout_seconds || 180;
  document.querySelector("#ai-remove-background").checked = Boolean(values.remove_background);
  document.querySelector("#ai-key").value = "";
  document.querySelector("#ai-key").placeholder = values.configured
    ? `已配置（${values.api_key_mask}），留空表示不修改`
    : "尚未配置 API Key";
}

async function loadAiSettings() {
  try {
    const response = await fetch("/api/settings/ai", { headers: adminHeaders() });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "读取 AI 配置失败");
    fillAiSettings(payload);
    setAiSettingsStatus("已读取");
  } catch (error) {
    setAiSettingsStatus(error.message || String(error), true);
  }
}

loadAiSettingsButton.addEventListener("click", loadAiSettings);

aiSettingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const key = document.querySelector("#ai-key").value.trim();
    const body = {
      enabled: document.querySelector("#ai-enabled").checked,
      api_base_url: document.querySelector("#ai-base-url").value.trim(),
      image_model: document.querySelector("#ai-model").value.trim(),
      frame_count: Number(document.querySelector("#ai-frame-count").value),
      max_references: Number(document.querySelector("#ai-max-references").value),
      timeout_seconds: Number(document.querySelector("#ai-timeout").value),
      remove_background: document.querySelector("#ai-remove-background").checked,
      clear_api_key: document.querySelector("#ai-clear-key").checked,
    };
    if (key) body.api_key = key;
    const response = await fetch("/api/settings/ai", {
      method: "PUT",
      headers: { ...adminHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "保存 AI 配置失败");
    fillAiSettings(payload);
    document.querySelector("#ai-clear-key").checked = false;
    setAiSettingsStatus("已保存，后续任务将使用新配置");
  } catch (error) {
    setAiSettingsStatus(error.message || String(error), true);
  }
});

function showProgress(job) {
  progress.classList.remove("hidden");
  statusText.textContent = job.status || "processing";
  const percent = Math.max(0, Math.min(100, Number(job.progress || 0)));
  percentText.textContent = `${percent}%`;
  barFill.style.width = `${percent}%`;
  messageText.textContent = job.message || "";
  if (job.download_url) {
    download.href = job.download_url;
    download.classList.remove("hidden");
    download.textContent = job.artifact_kind === "exe" ? "下载 Windows exe" : "下载资源包 zip";
  }
}

async function poll(jobId) {
  const response = await fetch(`/api/jobs/${jobId}`);
  const job = await response.json();
  if (!response.ok) throw new Error(job.detail || "读取任务失败");
  showProgress(job);
  if (["ready", "failed"].includes(job.status)) {
    submitButton.disabled = false;
    return;
  }
  window.setTimeout(() => poll(jobId).catch(showError), 1500);
}

function showError(error) {
  submitButton.disabled = false;
  progress.classList.remove("hidden");
  statusText.textContent = "failed";
  messageText.textContent = error.message || String(error);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const files = document.querySelector("#photos").files;
  if (!files.length) return;
  submitButton.disabled = true;
  download.classList.add("hidden");
  const body = new FormData(form);
  body.delete("photos");
  for (const file of files) body.append("photos", file);
  body.set("build_exe", document.querySelector("#build-exe").checked ? "true" : "false");
  try {
    const response = await fetch("/api/pets/generate", { method: "POST", body });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "提交失败");
    showProgress(payload);
    await poll(payload.id);
  } catch (error) {
    showError(error);
  }
});
