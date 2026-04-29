export const MS_PER_DAY = 24 * 60 * 60 * 1000;
export const SHANGHAI_TIME_ZONE = 'Asia/Shanghai';
export const DEFAULT_ISSUE_THRESHOLDS = {
  bugDays: 5,
  featureDays: 30
};

export function getAgeInDays(createdAt, now = new Date()) {
  const createdTime = new Date(createdAt).getTime();
  const nowTime = new Date(now).getTime();

  if (!Number.isFinite(createdTime) || !Number.isFinite(nowTime)) {
    return 0;
  }

  return Math.max(0, (nowTime - createdTime) / MS_PER_DAY);
}

export function isStaleByCreatedAt(createdAt, staleDays, now = new Date()) {
  return getAgeInDays(createdAt, now) > Number(staleDays);
}

function keywordMatch(values, keywords) {
  return values.some((value) => {
    const normalized = String(value || '').trim().toLowerCase();
    return normalized && keywords.some((keyword) => normalized.includes(keyword));
  });
}

function deriveIssueCategories(values) {
  const categories = [];

  if (keywordMatch(values, ['bug', 'bug-report', 'defect', '缺陷', '故障', '问题'])) {
    categories.push('bug');
  }

  if (keywordMatch(values, ['feature', 'feature-request', 'enhancement', '需求', '功能'])) {
    categories.push('feature');
  }

  return categories;
}

export function normalizeIssueThresholds(issueThresholds) {
  const bugDays = Number(issueThresholds?.bugDays);
  const featureDays = Number(issueThresholds?.featureDays);

  return {
    bugDays: Number.isFinite(bugDays) && bugDays > 0 ? bugDays : DEFAULT_ISSUE_THRESHOLDS.bugDays,
    featureDays: Number.isFinite(featureDays) && featureDays > 0 ? featureDays : DEFAULT_ISSUE_THRESHOLDS.featureDays
  };
}

export function getIssueAttentionState(issue, issueThresholds, now = new Date()) {
  const thresholds = normalizeIssueThresholds(issueThresholds);
  const labelNames = Array.isArray(issue?.labels) ? issue.labels : [];
  const categoryNames = Array.isArray(issue?.categories) ? issue.categories : [];
  const explicitCategories = deriveIssueCategories([
    ...labelNames,
    ...categoryNames,
    issue?.title
  ]);
  const fallbackCategories = explicitCategories.length > 0
    ? explicitCategories
    : deriveIssueCategories([issue?.issueType]);
  const isBug = issue?.isBug ?? fallbackCategories.includes('bug');
  const isFeature = issue?.isFeature ?? fallbackCategories.includes('feature');
  const isBugStale = issue?.isBugStale ?? (isBug && isStaleByCreatedAt(issue?.createdAt, thresholds.bugDays, now));
  const isFeatureStale = issue?.isFeatureStale ?? (isFeature && isStaleByCreatedAt(issue?.createdAt, thresholds.featureDays, now));

  return {
    isBug: Boolean(isBug),
    isFeature: Boolean(isFeature),
    isBugStale: Boolean(isBugStale),
    isFeatureStale: Boolean(isFeatureStale),
    needsAttention: Boolean(issue?.needsAttention ?? (isBugStale || isFeatureStale))
  };
}

export function formatDateTime(value) {
  if (!value) {
    return '-';
  }

  const date = new Date(value);

  if (!Number.isFinite(date.getTime())) {
    return '-';
  }

  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: SHANGHAI_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false
  }).format(date);
}

export function formatOpenDuration(ageDaysExact) {
  const days = Number(ageDaysExact);

  if (!Number.isFinite(days) || days < 0) {
    return '-';
  }

  if (days < 1) {
    const hours = Math.floor(days * 24);
    return `${hours} 小时`;
  }

  return `${Math.floor(days)} 天`;
}
