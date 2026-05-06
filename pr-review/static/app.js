const form = document.querySelector("#reviewForm");
const prUrlInput = document.querySelector("#prUrl");
const scmTokenInput = document.querySelector("#scmToken");
const modelInput = document.querySelector("#model");
const reviewButton = document.querySelector("#reviewButton");
const publishButton = document.querySelector("#publishButton");
const serviceState = document.querySelector("#serviceState");
const healthBadge = document.querySelector("#healthBadge");
const totalCount = document.querySelector("#totalCount");
const severeCount = document.querySelector("#severeCount");
const suggestionCount = document.querySelector("#suggestionCount");
const styleCount = document.querySelector("#styleCount");
const summaryText = document.querySelector("#summaryText");
const warningList = document.querySelector("#warningList");
const commentsList = document.querySelector("#commentsList");
const selectAll = document.querySelector("#selectAll");

let currentComments = [];

checkHealth();

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  await generateReview();
});

publishButton.addEventListener("click", async () => {
  await publishSelected();
});

selectAll.addEventListener("change", () => {
  document.querySelectorAll(".comment-select:not(:disabled)").forEach((checkbox) => {
    checkbox.checked = selectAll.checked;
  });
});

async function checkHealth() {
  try {
    const response = await fetch("/api/health");
    if (!response.ok) throw new Error("health failed");
    healthBadge.className = "status-pill ok";
    healthBadge.textContent = "API 正常";
  } catch {
    healthBadge.className = "status-pill error";
    healthBadge.textContent = "API 异常";
  }
}

async function generateReview() {
  setBusy(true, "正在生成 review...");
  currentComments = [];
  clearPublishState();

  try {
    const response = await fetch("/api/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pr_url: prUrlInput.value.trim(),
        scm_token: scmTokenInput.value.trim() || null,
        model: modelInput.value.trim() || null,
      }),
    });
    const payload = await parseResponse(response);
    currentComments = payload.comments || [];
    renderSummary(payload.summary, payload.warnings || []);
    renderComments(currentComments);
    serviceState.textContent = payload.pr?.title ? `${payload.pr.repository} !${payload.pr.number} · ${payload.pr.title}` : "Review 已生成";
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
}

async function publishSelected() {
  const selectedIds = new Set(
    Array.from(document.querySelectorAll(".comment-select:checked")).map((checkbox) => checkbox.value),
  );
  const comments = currentComments.filter((comment) => selectedIds.has(comment.id));
  if (!comments.length) {
    showError("没有选中的可发布意见。");
    return;
  }

  setBusy(true, "正在发布评论...");
  try {
    const response = await fetch("/api/publish", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pr_url: prUrlInput.value.trim(),
        scm_token: scmTokenInput.value.trim() || null,
        comments,
      }),
    });
    const payload = await parseResponse(response);
    renderPublishResults(payload.results || []);
    serviceState.textContent = "发布完成";
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
}

function renderSummary(summary, warnings) {
  totalCount.textContent = summary?.total ?? 0;
  severeCount.textContent = summary?.severe ?? 0;
  suggestionCount.textContent = summary?.suggestion ?? 0;
  styleCount.textContent = summary?.style ?? 0;
  summaryText.textContent = summary?.text || "没有 summary。";

  warningList.replaceChildren();
  if (warnings.length) {
    warningList.hidden = false;
    warnings.forEach((warning) => {
      const item = document.createElement("div");
      item.textContent = warning;
      warningList.appendChild(item);
    });
  } else {
    warningList.hidden = true;
  }
}

function renderComments(comments) {
  commentsList.replaceChildren();
  if (!comments.length) {
    const emptyState = document.createElement("div");
    emptyState.className = "empty-state";
    emptyState.textContent = "没有发现需要提交的 review 意见。";
    commentsList.appendChild(emptyState);
    publishButton.disabled = true;
    selectAll.disabled = true;
    return;
  }

  comments.forEach((comment) => {
    const card = document.createElement("article");
    card.className = "comment-card";
    card.dataset.commentId = comment.id;

    const checkbox = document.createElement("input");
    checkbox.className = "comment-select";
    checkbox.type = "checkbox";
    checkbox.value = comment.id;
    checkbox.checked = comment.publishable;
    checkbox.disabled = !comment.publishable;

    const main = document.createElement("div");
    main.className = "comment-main";
    main.appendChild(renderCommentHeader(comment));
    main.appendChild(renderCommentBody(comment));

    card.appendChild(checkbox);
    card.appendChild(main);
    commentsList.appendChild(card);
  });

  publishButton.disabled = !comments.some((comment) => comment.publishable);
  selectAll.disabled = publishButton.disabled;
  selectAll.checked = true;
}

function renderCommentHeader(comment) {
  const header = document.createElement("div");
  header.className = "comment-header";

  const fileLine = document.createElement("span");
  fileLine.className = "file-line";
  fileLine.textContent = `${comment.file_path}:${comment.line}`;
  header.appendChild(fileLine);

  const category = document.createElement("span");
  category.className = "badge category";
  category.textContent = comment.category;
  header.appendChild(category);

  const severity = document.createElement("span");
  severity.className = `badge ${severityClass(comment.severity)}`;
  severity.textContent = comment.severity;
  header.appendChild(severity);

  return header;
}

function renderCommentBody(comment) {
  const body = document.createElement("div");
  body.className = "comment-body";

  const message = document.createElement("p");
  const messageLabel = document.createElement("strong");
  messageLabel.textContent = "问题：";
  message.appendChild(messageLabel);
  message.appendChild(document.createTextNode(comment.message));
  body.appendChild(message);

  const suggestion = document.createElement("p");
  const suggestionLabel = document.createElement("strong");
  suggestionLabel.textContent = "建议：";
  suggestion.appendChild(suggestionLabel);
  suggestion.appendChild(document.createTextNode(comment.suggestion));
  body.appendChild(suggestion);

  const pre = document.createElement("pre");
  const code = document.createElement("code");
  code.textContent = comment.code_example;
  pre.appendChild(code);
  body.appendChild(pre);

  const state = document.createElement("p");
  state.className = "publish-state";
  state.dataset.publishState = comment.id;
  state.textContent = comment.publishable ? "待发布" : comment.publish_warning || "不可发布";
  body.appendChild(state);

  return body;
}

function renderPublishResults(results) {
  const byId = new Map(results.map((result) => [result.id, result]));
  document.querySelectorAll("[data-publish-state]").forEach((node) => {
    const result = byId.get(node.dataset.publishState);
    if (!result) return;
    node.classList.remove("error", "published");
    if (result.status === "published") {
      node.classList.add("published");
      node.textContent = result.url ? `已发布：${result.url}` : "已发布";
    } else if (result.status === "skipped") {
      node.textContent = result.error || "已跳过";
    } else {
      node.classList.add("error");
      node.textContent = result.error || "发布失败";
    }
  });
}

function clearPublishState() {
  publishButton.disabled = true;
  selectAll.disabled = true;
}

async function parseResponse(response) {
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const message = typeof payload === "string" ? payload : payload.detail || "请求失败";
    throw new Error(message);
  }
  return payload;
}

function setBusy(isBusy, text) {
  reviewButton.disabled = isBusy;
  publishButton.disabled = isBusy || !currentComments.some((comment) => comment.publishable);
  serviceState.textContent = isBusy ? text : serviceState.textContent;
}

function showError(message) {
  serviceState.textContent = message;
  summaryText.textContent = message;
}

function severityClass(severity) {
  if (severity === "严重") return "severe";
  if (severity === "规范") return "style";
  return "suggestion";
}

