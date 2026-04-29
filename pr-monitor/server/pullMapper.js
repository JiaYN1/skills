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

function parseTimestamp(value) {
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : 0;
}

function commitTimestamp(commit) {
  return parseTimestamp(firstNonEmpty([
    commit?.committed_at,
    commit?.created_at,
    commit?.commit?.committer?.date,
    commit?.commit?.author?.date
  ]));
}

function latestCommit(commits) {
  if (!Array.isArray(commits) || commits.length === 0) {
    return null;
  }

  let latest = commits[commits.length - 1];
  let latestTime = commitTimestamp(latest);

  for (const commit of commits) {
    const time = commitTimestamp(commit);
    if (time > latestTime) {
      latest = commit;
      latestTime = time;
    }
  }

  return latest;
}

function lastModifierFromCommits(commits) {
  const commit = latestCommit(commits);

  if (!commit) {
    return '-';
  }

  return displayName(commit.author)
    || displayName(commit.committer)
    || displayName(commit.commit?.author)
    || displayName(commit.commit?.committer)
    || '-';
}

function normalizeDate(value, fallback) {
  const date = new Date(value || fallback);
  return Number.isFinite(date.getTime()) ? date.toISOString() : fallback;
}

function pullNumber(pull) {
  const candidate = pull?.number ?? pull?.iid ?? pull?.id;
  const parsed = Number(candidate);

  if (!Number.isInteger(parsed)) {
    throw new Error('Pull request is missing a numeric number.');
  }

  return parsed;
}

function pullUrl(repo, pull, number) {
  return firstNonEmpty([
    pull?.html_url,
    pull?.web_url
  ]) || `https://gitcode.com/${repo.owner}/${repo.repo}/merge_requests/${number}`;
}

export function normalizePull(repo, pull, commits, refreshedAt) {
  const number = pullNumber(pull);
  const createdAt = normalizeDate(pull?.created_at || pull?.createdAt, refreshedAt);

  return {
    number,
    title: firstNonEmpty([pull?.title]) || `#${number}`,
    state: firstNonEmpty([pull?.state]) || 'open',
    author: displayName(pull?.user)
      || displayName(pull?.author)
      || displayName(pull?.creator)
      || '-',
    lastModifier: lastModifierFromCommits(commits),
    createdAt,
    updatedAt: normalizeDate(pull?.updated_at || pull?.updatedAt, createdAt),
    htmlUrl: pullUrl(repo, pull, number)
  };
}
