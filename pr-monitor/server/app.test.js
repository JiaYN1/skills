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

  it('returns 409 when a manual refresh is already running', async () => {
    const db = initDb(':memory:');
    const app = await createServer({
      db,
      repos: [],
      staleDays: 5,
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
