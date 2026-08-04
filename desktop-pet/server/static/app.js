const form = document.querySelector("#pet-form");
const submitButton = document.querySelector("#submit");
const progress = document.querySelector("#progress");
const statusText = document.querySelector("#status");
const percentText = document.querySelector("#percent");
const messageText = document.querySelector("#message");
const barFill = document.querySelector("#bar-fill");
const download = document.querySelector("#download");
const buildExeButton = document.querySelector("#build-exe-button");
const adminTokenInput = document.querySelector("#admin-token");
const aiSettingsForm = document.querySelector("#ai-settings-form");
const loadAiSettingsButton = document.querySelector("#load-ai-settings");
const aiSettingsStatus = document.querySelector("#ai-settings-status");
let currentJobId = null;
let currentJob = null;

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
  document.querySelector("#ai-walk-frame-count").value = values.walk_frame_count || 12;
  document.querySelector("#ai-sleep-frame-count").value = values.sleep_frame_count || 10;
  document.querySelector("#ai-animation-fps").value = values.animation_fps || 12;
  document.querySelector("#ai-animation-mode").value = values.animation_mode || "hybrid";
  document.querySelector("#ai-pose-consistency").checked = values.pose_consistency !== false;
  document.querySelector("#ai-remove-background").checked = Boolean(values.remove_background);
  document.querySelector("#ai-rembg-model").value = values.rembg_model || "isnet-general-use";
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
      walk_frame_count: Number(document.querySelector("#ai-walk-frame-count").value),
      sleep_frame_count: Number(document.querySelector("#ai-sleep-frame-count").value),
      animation_fps: Number(document.querySelector("#ai-animation-fps").value),
      animation_mode: document.querySelector("#ai-animation-mode").value,
      pose_consistency: document.querySelector("#ai-pose-consistency").checked,
      remove_background: document.querySelector("#ai-remove-background").checked,
      rembg_model: document.querySelector("#ai-rembg-model").value.trim(),
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
  currentJob = job;
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
  buildExeButton.disabled = !(
    currentJobId && job.status === "ready" && job.artifact_kind === "zip"
  );
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
  buildExeButton.disabled = !(
    currentJobId && currentJob && currentJob.status === "ready" && currentJob.artifact_kind === "zip"
  );
  progress.classList.remove("hidden");
  statusText.textContent = "failed";
  messageText.textContent = error.message || String(error);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const files = document.querySelector("#photos").files;
  if (!files.length) return;
  submitButton.disabled = true;
  buildExeButton.disabled = true;
  currentJobId = null;
  currentJob = null;
  download.classList.add("hidden");
  const body = new FormData(form);
  body.delete("photos");
  for (const file of files) body.append("photos", file);
  body.set("build_exe", "false");
  try {
    const response = await fetch("/api/pets/generate", { method: "POST", body });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "提交失败");
    currentJobId = payload.id;
    showProgress(payload);
    await poll(payload.id);
  } catch (error) {
    showError(error);
  }
});

buildExeButton.addEventListener("click", async () => {
  if (!currentJobId) return;
  buildExeButton.disabled = true;
  submitButton.disabled = true;
  try {
    const response = await fetch(`/api/jobs/${currentJobId}/build-exe`, { method: "POST" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "提交 exe 打包请求失败");
    showProgress(payload);
    await poll(currentJobId);
  } catch (error) {
    showError(error);
  }
});
