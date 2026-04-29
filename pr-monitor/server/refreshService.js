import { normalizePull } from './pullMapper.js';
import { normalizeIssue } from './issueMapper.js';
import { getRepoKey } from './db.js';

export class RefreshAlreadyRunningError extends Error {
  constructor() {
    super('Refresh is already running.');
    this.name = 'RefreshAlreadyRunningError';
  }
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

export function createRefreshService({ db, client, repos, clock = () => new Date() }) {
  let currentRefresh = null;

  async function refreshRepo(repo, refreshedAt) {
    const pulls = await client.listOpenPulls(repo);
    const pullRows = [];

    for (const pull of pulls) {
      const number = Number(pull?.number ?? pull?.iid ?? pull?.id);
      const commits = await client.listPullCommits(repo, number);
      pullRows.push(normalizePull(repo, pull, commits, refreshedAt));
    }

    db.replaceRepoPulls(repo, pullRows, refreshedAt);

    const issues = await client.listOpenIssues(repo);
    const issueRows = issues.map((issue) => normalizeIssue(repo, issue, refreshedAt));

    db.replaceRepoIssues(repo, issueRows, refreshedAt);
    db.setRepoStatus(repo, 'success', null, refreshedAt);

    return {
      pullCount: pullRows.length,
      issueCount: issueRows.length
    };
  }

  async function runRefresh() {
    const startedAt = clock().toISOString();
    db.markRefreshStarted(startedAt);

    const failures = [];
    const results = [];

    if (repos.length === 0) {
      const finishedAt = clock().toISOString();
      const message = 'No repositories configured in config/repos.json.';
      db.markRefreshFinished({ finishedAt, lastError: message });
      return { ok: false, results, failures: [{ repoKey: null, message }] };
    }

    for (const repo of repos) {
      const repoKey = getRepoKey(repo);
      const refreshedAt = clock().toISOString();

      try {
        const counts = await refreshRepo(repo, refreshedAt);
        results.push({ repoKey, ...counts });
      } catch (error) {
        const message = errorMessage(error);
        failures.push({ repoKey, message });
        db.setRepoStatus(repo, 'error', message, refreshedAt);
      }
    }

    const finishedAt = clock().toISOString();
    const lastError = failures.length
      ? failures.map((failure) => `${failure.repoKey}: ${failure.message}`).join('\n')
      : null;
    db.markRefreshFinished({ finishedAt, lastError });

    return {
      ok: failures.length === 0,
      results,
      failures
    };
  }

  return {
    isRefreshing() {
      return Boolean(currentRefresh);
    },

    async refreshAll() {
      if (currentRefresh) {
        throw new RefreshAlreadyRunningError();
      }

      currentRefresh = runRefresh();

      try {
        return await currentRefresh;
      } finally {
        currentRefresh = null;
      }
    }
  };
}
