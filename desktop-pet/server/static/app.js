const form = document.querySelector("#pet-form");
const submitButton = document.querySelector("#submit");
const progress = document.querySelector("#progress");
const statusText = document.querySelector("#status");
const percentText = document.querySelector("#percent");
const messageText = document.querySelector("#message");
const barFill = document.querySelector("#bar-fill");
const download = document.querySelector("#download");
const buildExeButton = document.querySelector("#build-exe-button");
const previewSection = document.querySelector("#preview-section");
const previewGrid = document.querySelector("#preview-grid");
const confirmPreviewButton = document.querySelector("#confirm-preview");
const restartPreviewButton = document.querySelector("#restart-preview");
const resourcePreviewSection = document.querySelector("#resource-preview-section");
const resourcePreviewGrid = document.querySelector("#resource-preview-grid");
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
  document.querySelector("#ai-key").value = "";
  document.querySelector("#ai-key").placeholder = values.configured
    ? `已配置（${values.api_key_mask}），留空表示不修改`
    : "尚未配置 API Key";
}

function renderPreviewImages(items, section, grid, canRegenerate) {
  grid.replaceChildren();
  if (!Array.isArray(items) || !items.length) {
    section.classList.add("hidden");
    return;
  }
  for (const item of items) {
    const figure = document.createElement("figure");
    figure.className = "preview-item";
    const image = document.createElement("img");
    image.src = item.url;
    image.alt = item.name || "宠物预览";
    image.loading = "lazy";
    const caption = document.createElement("figcaption");
    caption.textContent = item.name || "预览图片";
    figure.append(image, caption);
    if (item.regenerate_url) {
      const actions = document.createElement("div");
      actions.className = "preview-item-actions";
      const regenerate = document.createElement("button");
      regenerate.type = "button";
      regenerate.className = "secondary preview-regenerate";
      regenerate.textContent = "重新生成";
      regenerate.disabled = !canRegenerate;
      regenerate.addEventListener("click", () => regeneratePreview(item, regenerate));
      actions.append(regenerate);
      figure.append(actions);
    }
    grid.append(figure);
  }
  section.classList.remove("hidden");
}

async function regeneratePreview(item, button) {
  if (!currentJobId || !item.regenerate_url || button.disabled) return;
  button.disabled = true;
  submitButton.disabled = true;
  confirmPreviewButton.disabled = true;
  restartPreviewButton.disabled = true;
  buildExeButton.disabled = true;
  try {
    const response = await fetch(item.regenerate_url, { method: "POST" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "重新生成预览失败");
    showProgress(payload);
    await poll(currentJobId);
  } catch (error) {
    showError(error);
  }
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
  statusText.textContent = job.status === "preview_ready" ? "等待确认预览" : (job.status || "processing");
  const percent = Math.max(0, Math.min(100, Number(job.progress || 0)));
  percentText.textContent = `${percent}%`;
  barFill.style.width = `${percent}%`;
  messageText.textContent = job.message || "";
  if (job.download_url) {
    download.href = job.download_url;
    download.classList.remove("hidden");
    download.textContent = job.artifact_kind === "exe" ? "下载 Windows exe" : "下载资源包 zip";
  }
  renderPreviewImages(
    job.preview_images,
    previewSection,
    previewGrid,
    job.status === "preview_ready",
  );
  renderPreviewImages(
    job.resource_preview_images,
    resourcePreviewSection,
    resourcePreviewGrid,
    job.status === "ready" && job.artifact_kind === "zip",
  );
  confirmPreviewButton.disabled = job.status !== "preview_ready";
  restartPreviewButton.disabled = job.status !== "preview_ready";
  buildExeButton.disabled = !(
    currentJobId && job.status === "ready" && job.artifact_kind === "zip"
  );
}

async function poll(jobId) {
  const response = await fetch(`/api/jobs/${jobId}`);
  const job = await response.json();
  if (!response.ok) throw new Error(job.detail || "读取任务失败");
  showProgress(job);
  if (job.status === "preview_ready") {
    submitButton.disabled = true;
    return;
  }
  if (["ready", "failed"].includes(job.status)) {
    submitButton.disabled = false;
    return;
  }
  window.setTimeout(() => poll(jobId).catch(showError), 1500);
}

function showError(error) {
  submitButton.disabled = false;
  confirmPreviewButton.disabled = true;
  restartPreviewButton.disabled = false;
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
  previewSection.classList.add("hidden");
  resourcePreviewSection.classList.add("hidden");
  previewGrid.replaceChildren();
  resourcePreviewGrid.replaceChildren();
  confirmPreviewButton.disabled = true;
  restartPreviewButton.disabled = true;
  const body = new FormData(form);
  body.delete("photos");
  for (const file of files) body.append("photos", file);
  try {
    const response = await fetch("/api/pets/prepare", { method: "POST", body });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "提交失败");
    currentJobId = payload.id;
    showProgress(payload);
    await poll(payload.id);
  } catch (error) {
    showError(error);
  }
});

confirmPreviewButton.addEventListener("click", async () => {
  if (!currentJobId || !currentJob || currentJob.status !== "preview_ready") return;
  confirmPreviewButton.disabled = true;
  submitButton.disabled = true;
  try {
    const response = await fetch(`/api/jobs/${currentJobId}/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: document.querySelector("#pet-name").value.trim() }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "生成动作资源失败");
    showProgress(payload);
    await poll(currentJobId);
  } catch (error) {
    showError(error);
  }
});

restartPreviewButton.addEventListener("click", () => {
  currentJobId = null;
  currentJob = null;
  document.querySelector("#photos").value = "";
  previewSection.classList.add("hidden");
  resourcePreviewSection.classList.add("hidden");
  previewGrid.replaceChildren();
  resourcePreviewGrid.replaceChildren();
  confirmPreviewButton.disabled = true;
  restartPreviewButton.disabled = true;
  submitButton.disabled = false;
  buildExeButton.disabled = true;
  progress.classList.add("hidden");
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
