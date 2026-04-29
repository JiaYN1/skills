function firstNonEmpty(values) {
  for (const value of values) {
    if (value !== undefined && value !== null && String(value).trim()) {
      return String(value).trim();
    }
  }

  return '';
}

function displayName(entity) {
  if (!entity) {
    return '';
  }

  if (typeof entity === 'string') {
    return entity.trim();
  }

  return firstNonEmpty([
    entity.login,
    entity.username,
    entity.name,
    entity.full_name,
    entity.email
  ]);
}

function normalizeDate(value, fallback) {
  const date = new Date(value || fallback);
  return Number.isFinite(date.getTime()) ? date.toISOString() : fallback;
}

function issueNumber(issue) {
  const candidate = issue?.number ?? issue?.iid ?? issue?.id;
  const parsed = Number(candidate);

  if (!Number.isInteger(parsed)) {
    throw new Error('Issue is missing a numeric number.');
  }

  return parsed;
}

function issueUrl(repo, issue, number) {
  return firstNonEmpty([
    issue?.html_url,
    issue?.web_url
  ]) || `https://gitcode.com/${repo.owner}/${repo.repo}/issues/${number}`;
}

function normalizeLabelName(label) {
  if (!label) {
    return '';
  }

  if (typeof label === 'string') {
    return label.trim();
  }

  return firstNonEmpty([label.name, label.title]);
}

function uniqueLabels(labels) {
  const deduped = new Map();

  for (const label of Array.isArray(labels) ? labels : []) {
    const name = normalizeLabelName(label);

    if (!name) {
      continue;
    }

    const key = name.toLowerCase();
    if (!deduped.has(key)) {
      deduped.set(key, name);
    }
  }

  return [...deduped.values()];
}

function hasKeyword(values, keywords) {
  return values.some((value) => {
    const normalized = String(value || '').trim().toLowerCase();
    return normalized && keywords.some((keyword) => normalized.includes(keyword));
  });
}

function categoriesFromValues(values) {
  const bugKeywords = ['bug', 'bug-report', 'defect', '缺陷', '故障', '问题'];
  const featureKeywords = ['feature', 'feature-request', 'enhancement', '需求', '功能'];
  const categories = [];

  if (hasKeyword(values, bugKeywords)) {
    categories.push('bug');
  }

  if (hasKeyword(values, featureKeywords)) {
    categories.push('feature');
  }

  return categories;
}

function deriveCategories({ labels, issueType, title }) {
  const normalizedLabels = labels.map((label) => label.toLowerCase());
  const labelAndTitleCategories = categoriesFromValues([...normalizedLabels, title]);

  if (labelAndTitleCategories.length > 0) {
    return labelAndTitleCategories;
  }

  return categoriesFromValues([issueType]);
}

export function normalizeIssue(repo, issue, refreshedAt) {
  const number = issueNumber(issue);
  const createdAt = normalizeDate(issue?.created_at || issue?.createdAt, refreshedAt);
  const labels = uniqueLabels(issue?.labels);
  const issueType = firstNonEmpty([
    issue?.issue_type_detail?.title,
    issue?.issue_type,
    issue?.type
  ]);
  const categories = deriveCategories({
    labels,
    issueType,
    title: issue?.title
  });

  return {
    number,
    title: firstNonEmpty([issue?.title]) || `#${number}`,
    state: firstNonEmpty([issue?.state]) || 'open',
    author: displayName(issue?.user)
      || displayName(issue?.author)
      || displayName(issue?.creator)
      || '-',
    createdAt,
    updatedAt: normalizeDate(issue?.updated_at || issue?.updatedAt, createdAt),
    htmlUrl: issueUrl(repo, issue, number),
    issueType,
    labels,
    isBug: categories.includes('bug'),
    isFeature: categories.includes('feature')
  };
}
