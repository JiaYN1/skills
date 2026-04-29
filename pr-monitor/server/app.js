import fs from 'node:fs';
import path from 'node:path';
import Fastify from 'fastify';
import fastifyStatic from '@fastify/static';
import { getRepoKey } from './db.js';
import { getAgeInDays, getWholeAgeDays, isOlderThanDays } from './age.js';
import { RefreshAlreadyRunningError } from './refreshService.js';

function mapPull(row, staleDays, now = new Date()) {
  const ageDaysExact = getAgeInDays(row.created_at, now);

  return {
    repoOwner: row.repo_owner,
    repoName: row.repo_name,
    repoLabel: row.repo_label,
    number: row.number,
    title: row.title,
    state: row.state,
    author: row.author,
    lastModifier: row.last_modifier,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    htmlUrl: row.html_url,
    refreshedAt: row.refreshed_at,
    ageDays: getWholeAgeDays(row.created_at, now),
    ageDaysExact,
    isStale: isOlderThanDays(row.created_at, staleDays, now)
  };
}

function parseJsonArray(value) {
  if (!value) {
    return [];
  }

  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function issueCategories(row) {
  const categories = [];

  if (Boolean(row.is_bug)) {
    categories.push('bug');
  }

  if (Boolean(row.is_feature)) {
    categories.push('feature');
  }

  return categories;
}

function mapIssue(row, issueThresholds, now = new Date()) {
  const ageDaysExact = getAgeInDays(row.created_at, now);
  const isBug = Boolean(row.is_bug);
  const isFeature = Boolean(row.is_feature);
  const isBugStale = isBug && isOlderThanDays(row.created_at, issueThresholds.bugDays, now);
  const isFeatureStale = isFeature && isOlderThanDays(row.created_at, issueThresholds.featureDays, now);

  return {
    repoOwner: row.repo_owner,
    repoName: row.repo_name,
    repoLabel: row.repo_label,
    number: row.number,
    title: row.title,
    state: row.state,
    author: row.author,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    htmlUrl: row.html_url,
    issueType: row.issue_type,
    labels: parseJsonArray(row.labels_json),
    categories: issueCategories(row),
    refreshedAt: row.refreshed_at,
    ageDays: getWholeAgeDays(row.created_at, now),
    ageDaysExact,
    isBug,
    isFeature,
    isBugStale,
    isFeatureStale,
    needsAttention: isBugStale || isFeatureStale
  };
}

function mapStatus(db, refreshService, repos, staleDays, issueThresholds) {
  const state = db.getRefreshStatus();
  const repoStates = new Map(db.getRepoStatuses().map((row) => [row.repo_key, row]));

  return {
    isRefreshing: refreshService.isRefreshing() || Boolean(state?.is_refreshing),
    lastStartedAt: state?.last_started_at || null,
    lastFinishedAt: state?.last_finished_at || null,
    lastSuccessAt: state?.last_success_at || null,
    lastError: state?.last_error || null,
    staleDays,
    issueThresholds,
    repos: repos.map((repo) => {
      const repoKey = getRepoKey(repo);
      const repoState = repoStates.get(repoKey);

      return {
        repoKey,
        owner: repo.owner,
        repo: repo.repo,
        label: repo.label,
        status: repoState?.status || 'pending',
        lastError: repoState?.last_error || null,
        refreshedAt: repoState?.refreshed_at || null
      };
    })
  };
}

export async function createServer({
  db,
  refreshService,
  repos,
  staleDays,
  issueThresholds = { bugDays: 5, featureDays: 30 },
  staticDir,
  logger = true
}) {
  const app = Fastify({ logger });

  app.get('/api/pulls', async () => {
    const now = new Date();
    return db.getPulls().map((row) => mapPull(row, staleDays, now));
  });

  app.get('/api/issues', async () => {
    const now = new Date();
    return db.getIssues().map((row) => mapIssue(row, issueThresholds, now));
  });

  app.get('/api/status', async () => mapStatus(db, refreshService, repos, staleDays, issueThresholds));

  app.post('/api/refresh', async (request, reply) => {
    try {
      const result = await refreshService.refreshAll();
      return {
        ok: result.ok,
        result,
        status: mapStatus(db, refreshService, repos, staleDays, issueThresholds)
      };
    } catch (error) {
      if (error instanceof RefreshAlreadyRunningError) {
        return reply.code(409).send({
          ok: false,
          error: error.message
        });
      }

      request.log.error(error);
      return reply.code(500).send({
        ok: false,
        error: error instanceof Error ? error.message : String(error)
      });
    }
  });

  if (staticDir && fs.existsSync(staticDir)) {
    await app.register(fastifyStatic, {
      root: staticDir,
      prefix: '/'
    });

    app.setNotFoundHandler((request, reply) => {
      if (request.raw.url?.startsWith('/api/')) {
        return reply.code(404).send({ error: 'Not found' });
      }

      return reply.sendFile('index.html');
    });
  }

  app.setErrorHandler((error, request, reply) => {
    request.log.error(error);
    reply.code(500).send({
      ok: false,
      error: 'Internal server error'
    });
  });

  return app;
}

export function resolveStaticDir(cwd = process.cwd()) {
  return path.join(cwd, 'dist');
}
