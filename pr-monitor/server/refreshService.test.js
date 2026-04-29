import { describe, expect, it } from 'vitest';
import { initDb } from './db.js';
import { createRefreshService, RefreshAlreadyRunningError } from './refreshService.js';

describe('refresh service', () => {
  it('keeps refreshing other repositories when one repository fails', async () => {
    const db = initDb(':memory:');
    const repos = [
      { owner: 'good', repo: 'repo', label: 'Good Repo' },
      { owner: 'bad', repo: 'repo', label: 'Bad Repo' }
    ];
    const client = {
      async listOpenPulls(repo) {
        if (repo.owner === 'bad') {
          throw new Error('boom');
        }

        return [
          {
            number: 1,
            title: 'Good PR',
            user: { login: 'alice' },
            created_at: '2026-04-28T00:00:00.000Z',
            updated_at: '2026-04-28T01:00:00.000Z',
            html_url: 'https://gitcode.com/good/repo/pull/1'
          }
        ];
      },
      async listPullCommits() {
        return [
          {
            author: { login: 'bob' },
            commit: { author: { date: '2026-04-28T01:00:00.000Z' } }
          }
        ];
      }
    };
    const service = createRefreshService({ db, client, repos });

    const result = await service.refreshAll();

    expect(result.ok).toBe(false);
    expect(result.results).toEqual([{ repoKey: 'good/repo', count: 1 }]);
    expect(result.failures).toEqual([{ repoKey: 'bad/repo', message: 'boom' }]);
    expect(db.getPulls()).toHaveLength(1);
    expect(db.getRepoStatuses().map((repo) => repo.status).sort()).toEqual(['error', 'success']);

    db.close();
  });

  it('prevents concurrent refresh runs', async () => {
    const db = initDb(':memory:');
    let resolvePulls;
    const client = {
      listOpenPulls: () => new Promise((resolve) => {
        resolvePulls = resolve;
      }),
      async listPullCommits() {
        return [];
      }
    };
    const service = createRefreshService({
      db,
      client,
      repos: [{ owner: 'o', repo: 'r', label: 'Repo' }]
    });

    const firstRefresh = service.refreshAll();
    await expect(service.refreshAll()).rejects.toBeInstanceOf(RefreshAlreadyRunningError);

    resolvePulls([]);
    await firstRefresh;
    db.close();
  });
});
