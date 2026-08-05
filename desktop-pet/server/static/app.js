const form = document.querySelector("#pet-form");
const submitButton = document.querySelector("#submit");
const progress = document.querySelector("#progress");
const statusText = document.querySelector("#status");
const percentText = document.querySelector("#percent");
const messageText = document.querySelector("#message");
const barFill = document.querySelector("#bar-fill");
const download = document.querySelector("#download");
const resumeGenerationButton = document.querySelector("#resume-generation-button");
const buildExeButton = document.querySelector("#build-exe-button");
const previewSection = document.querySelector("#preview-section");
const previewGrid = document.querySelector("#preview-grid");
const confirmPreviewButton = document.querySelector("#confirm-preview");
const restartPreviewButton = document.querySelector("#restart-preview");
const resourcePreviewSection = document.querySelector("#resource-preview-section");
const resourcePreviewGrid = document.querySelector("#resource-preview-grid");
const resourceSelectionText = document.querySelector("#resource-selection-text");
const removeSelectedFramesButton = document.querySelector("#remove-selected-frames");
const adminTokenInput = document.querySelector("#admin-token");
const aiSettingsForm = document.querySelector("#ai-settings-form");
const loadAiSettingsButton = document.querySelector("#load-ai-settings");
const aiSettingsStatus = document.querySelector("#ai-settings-status");
let currentJobId = null;
let currentJob = null;
let selectedResourceFrames = new Set();

function selectedActions() {
  return [...document.querySelectorAll('input[name="selected_actions"]')]
    .filter((input) => input.checked)
    .map((input) => input.value);
}

function applySelectedActions(values) {
  if (!Array.isArray(values) || !values.length) return;
  const selected = new Set(values);
  document.querySelectorAll('input[name="selected_actions"]').forEach((input) => {
    input.checked = selected.has(input.value);
  });
}

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
  document.querySelector("#ai-walk-frame-count").value = values.walk_frame_count || 16;
  document.querySelector("#ai-sleep-frame-count").value = values.sleep_frame_count || 12;
  document.querySelector("#ai-animation-fps").value = values.animation_fps || 12;
  document.querySelector("#ai-frame-repeat").value = values.frame_repeat || 1;
  document.querySelector("#ai-animation-mode").value = values.animation_mode || "hybrid";
  document.querySelector("#ai-pose-consistency").checked = values.pose_consistency !== false;
  document.querySelector("#ai-key").value = "";
  document.querySelector("#ai-key").placeholder = values.configured
    ? `已配置（${values.api_key_mask}），留空表示不修改`
    : "尚未配置 API Key";
}

function updateResourceSelectionControls(canDelete = false) {
  const count = selectedResourceFrames.size;
  resourceSelectionText.textContent = count
    ? `已选择 ${count} 帧`
    : "勾选突变帧删除，或点击帧下方按钮补帧";
  removeSelectedFramesButton.disabled = !canDelete || count === 0;
}

function renderPreviewImages(items, section, grid, canRegenerate, isResourcePreview = false) {
  grid.replaceChildren();
  if (!Array.isArray(items) || !items.length) {
    section.classList.add("hidden");
    if (isResourcePreview) {
      selectedResourceFrames.clear();
      updateResourceSelectionControls(false);
    }
    return;
  }
  if (isResourcePreview) {
    const available = new Set(
      items
        .filter((item) => item.role != null && item.index != null)
        .map((item) => `${item.role}:${item.index}`),
    );
    selectedResourceFrames = new Set(
      [...selectedResourceFrames].filter((key) => available.has(key)),
    );
  }
  for (const item of items) {
    const figure = document.createElement("figure");
    figure.className = "preview-item";
    const key = item.role != null && item.index != null
      ? `${item.role}:${item.index}`
      : null;
    if (isResourcePreview && key) {
      const selector = document.createElement("label");
      selector.className = "frame-select";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = selectedResourceFrames.has(key);
      checkbox.disabled = !canRegenerate;
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) selectedResourceFrames.add(key);
        else selectedResourceFrames.delete(key);
        figure.classList.toggle("selected", checkbox.checked);
        updateResourceSelectionControls(canRegenerate);
      });
      selector.append(checkbox, document.createTextNode("选择删除"));
      figure.classList.toggle("selected", checkbox.checked);
      figure.append(selector);
    }
    const image = document.createElement("img");
    image.src = item.url;
    image.alt = item.name || "宠物预览";
    image.loading = "lazy";
    const caption = document.createElement("figcaption");
    caption.textContent = item.name || "预览图片";
    figure.append(image, caption);
    const canInsertAfter = isResourcePreview && key && items.some(
      (candidate) => candidate.role === item.role && candidate.index === item.index + 1,
    );
    if (item.regenerate_url || canInsertAfter) {
      const actions = document.createElement("div");
      actions.className = "preview-item-actions";
      if (item.regenerate_url) {
        const regenerate = document.createElement("button");
        regenerate.type = "button";
        regenerate.className = "secondary preview-regenerate";
        regenerate.textContent = "重新生成";
        regenerate.disabled = !canRegenerate;
        regenerate.addEventListener("click", () => regeneratePreview(item, regenerate));
        actions.append(regenerate);
      }
      if (canInsertAfter) {
        const insert = document.createElement("button");
        insert.type = "button";
        insert.className = "secondary preview-insert";
        insert.textContent = "在此后插入一帧";
        insert.disabled = !canRegenerate;
        insert.addEventListener("click", () => insertResourceFrame(item, insert));
        actions.append(insert);
      }
      figure.append(actions);
    }
    grid.append(figure);
  }
  if (isResourcePreview) updateResourceSelectionControls(canRegenerate);
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

async function insertResourceFrame(item, button) {
  if (!currentJobId || item.role == null || item.index == null || button.disabled) return;
  if (!window.confirm(`在 ${item.name || `${item.role}_${item.index}.png`} 后插入一帧吗？`)) return;
  button.disabled = true;
  submitButton.disabled = true;
  buildExeButton.disabled = true;
  selectedResourceFrames.clear();
  updateResourceSelectionControls(false);
  try {
    const response = await fetch(`/api/jobs/${currentJobId}/previews/resource/insert`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role: item.role, after_index: item.index }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "插入动作帧失败");
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
      frame_repeat: Number(document.querySelector("#ai-frame-repeat").value),
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
  applySelectedActions(job.selected_actions);
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
  const canResume = Boolean(job.resume_url);
  resumeGenerationButton.classList.toggle("hidden", !canResume);
  resumeGenerationButton.disabled = !canResume || job.status !== "failed";
  const canBuildExe = canQueueExeBuild(job);
  buildExeButton.textContent =
    job.status === "failed" && job.artifact_kind === "zip"
      ? "重试打包 Windows exe"
      : "第二步：打包 Windows exe";
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
    true,
  );
  confirmPreviewButton.disabled = job.status !== "preview_ready";
  restartPreviewButton.disabled = job.status !== "preview_ready";
  buildExeButton.disabled = !canBuildExe;
}

async function removeSelectedFrames() {
  if (!currentJobId || !selectedResourceFrames.size || removeSelectedFramesButton.disabled) return;
  const frames = {};
  for (const key of selectedResourceFrames) {
    const separator = key.lastIndexOf(":");
    const role = key.slice(0, separator);
    const index = Number(key.slice(separator + 1));
    if (!frames[role]) frames[role] = [];
    frames[role].push(index);
  }
  const count = selectedResourceFrames.size;
  if (!window.confirm(`确定删除选中的 ${count} 帧吗？删除后会重排帧号并更新下载包。`)) return;
  removeSelectedFramesButton.disabled = true;
  submitButton.disabled = true;
  buildExeButton.disabled = true;
  try {
    const response = await fetch(`/api/jobs/${currentJobId}/previews/resource/remove`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ frames }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "删除动作帧失败");
    selectedResourceFrames.clear();
    showProgress(payload);
    await poll(currentJobId);
  } catch (error) {
    showError(error);
  }
}

removeSelectedFramesButton.addEventListener("click", removeSelectedFrames);

function canQueueExeBuild(job) {
  return Boolean(
    currentJobId &&
      job &&
      job.artifact_kind === "zip" &&
      ["ready", "failed"].includes(job.status),
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

resumeGenerationButton.addEventListener("click", async () => {
  if (!currentJobId || !currentJob || !currentJob.resume_url) return;
  resumeGenerationButton.disabled = true;
  submitButton.disabled = true;
  confirmPreviewButton.disabled = true;
  restartPreviewButton.disabled = true;
  buildExeButton.disabled = true;
  try {
    const response = await fetch(currentJob.resume_url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: document.querySelector("#pet-name").value.trim(),
        selected_actions: selectedActions(),
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "继续生成动作资源失败");
    showProgress(payload);
    await poll(currentJobId);
  } catch (error) {
    showError(error);
  }
});

function showError(error) {
  submitButton.disabled = false;
  confirmPreviewButton.disabled = true;
  restartPreviewButton.disabled = false;
  buildExeButton.disabled = !canQueueExeBuild(currentJob);
  resumeGenerationButton.disabled = !(
    currentJob && currentJob.status === "failed" && currentJob.resume_url
  );
  updateResourceSelectionControls(Boolean(
    currentJob && currentJob.status === "ready" && currentJob.artifact_kind === "zip",
  ));
  progress.classList.remove("hidden");
  statusText.textContent = "failed";
  messageText.textContent = error.message || String(error);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const files = document.querySelector("#photos").files;
  if (!files.length) return;
  const actions = selectedActions();
  if (!actions.length) {
    showError(new Error("至少选择一个动作"));
    return;
  }
  submitButton.disabled = true;
  buildExeButton.disabled = true;
  currentJobId = null;
  currentJob = null;
  download.classList.add("hidden");
  previewSection.classList.add("hidden");
  resourcePreviewSection.classList.add("hidden");
  resumeGenerationButton.classList.add("hidden");
  resumeGenerationButton.disabled = true;
  previewGrid.replaceChildren();
  resourcePreviewGrid.replaceChildren();
  selectedResourceFrames.clear();
  updateResourceSelectionControls(false);
  confirmPreviewButton.disabled = true;
  restartPreviewButton.disabled = true;
  const body = new FormData(form);
  body.delete("photos");
  body.delete("selected_actions");
  actions.forEach((action) => body.append("selected_actions", action));
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
      body: JSON.stringify({
        name: document.querySelector("#pet-name").value.trim(),
        selected_actions: selectedActions(),
      }),
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
  selectedResourceFrames.clear();
  updateResourceSelectionControls(false);
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
