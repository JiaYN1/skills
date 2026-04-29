import { describe, expect, it } from 'vitest';
import { createServer } from './app.js';
import { initDb } from './db.js';
import { RefreshAlreadyRunningError } from './refreshService.js';

describe('API', () => {
  it('returns cached pull requests', async () => {
    const db = initDb(':memory:');
    const repo = { owner: 'o', repo: 'r', label: 'Repo' };
    db.replaceRepoPulls(repo, [{
      number: 1,
      title: 'Cached PR',
      state: 'open',
      author: 'alice',
      lastModifier: '-',
      createdAt: '2026-04-20T00:00:00.000Z',
      updatedAt: '2026-04-21T00:00:00.000Z',
      htmlUrl: 'https://gitcode.com/o/r/pull/1'
    }], '2026-04-29T06:00:00.000Z');
    const app = await createServer({
      db,
      repos: [repo],
      staleDays: 5,
      issueThresholds: { bugDays: 5, featureDays: 30 },
      logger: false,
      refreshService: {
        isRefreshing: () => false,
        refreshAll: async () => ({ ok: true, results: [], failures: [] })
      }
    });

    const response = await app.inject({ method: 'GET', url: '/api/pulls' });
    const body = JSON.parse(response.body);

    expect(response.statusCode).toBe(200);
    expect(body).toHaveLength(1);
    expect(body[0].title).toBe('Cached PR');
    expect(body[0].repoLabel).toBe('Repo');

    await app.close();
    db.close();
  });

  it('returns cached issues with stale category flags', async () => {
    const db = initDb(':memory:');
    const repo = { owner: 'o', repo: 'r', label: 'Repo' };
    db.replaceRepoIssues(repo, [{
      number: 3,
      title: '[Feature] Cached issue',
      state: 'open',
      author: 'alice',
      createdAt: '2026-03-20T00:00:00.000Z',
      updatedAt: '2026-04-21T00:00:00.000Z',
      htmlUrl: 'https://gitcode.com/o/r/issues/3',
      issueType: '需求',
      labels: ['feature'],
      isBug: false,
      isFeature: true
    }], '2026-04-29T06:00:00.000Z');
    const app = await createServer({
      db,
      repos: [repo],
      staleDays: 5,
      issueThresholds: { bugDays: 5, featureDays: 30 },
      logger: false,
      refreshService: {
        isRefreshing: () => false,
        refreshAll: async () => ({ ok: true, results: [], failures: [] })
      }
    });

    const response = await app.inject({ method: 'GET', url: '/api/issues' });
    const body = JSON.parse(response.body);

    expect(response.statusCode).toBe(200);
    expect(body).toHaveLength(1);
    expect(body[0].labels).toEqual(['feature']);
    expect(body[0].isFeatureStale).toBe(true);
    expect(body[0].needsAttention).toBe(true);

    await app.close();
    db.close();
  });

  it('returns 409 when a manual refresh is already running', async () => {
    const db = initDb(':memory:');
    const app = await createServer({
      db,
      repos: [],
      staleDays: 5,
      issueThresholds: { bugDays: 5, featureDays: 30 },
      logger: false,
      refreshService: {
        isRefreshing: () => true,
        refreshAll: async () => {
          throw new RefreshAlreadyRunningError();
        }
      }
    });

    const response = await app.inject({ method: 'POST', url: '/api/refresh' });
    const body = JSON.parse(response.body);

    expect(response.statusCode).toBe(409);
    expect(body.error).toBe('Refresh is already running.');

    await app.close();
    db.close();
  });
});
