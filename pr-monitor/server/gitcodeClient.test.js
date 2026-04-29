import { describe, expect, it, vi } from 'vitest';
import { GitCodeClient } from './gitcodeClient.js';

describe('GitCodeClient', () => {
  it('fetches all open pull request pages with access_token query auth', async () => {
    const fetchImpl = vi.fn(async (url) => {
      const page = Number(url.searchParams.get('page'));
      const pulls = page === 1
        ? Array.from({ length: 100 }, (_, index) => ({ number: index + 1 }))
        : [{ number: 101 }];

      return Response.json(pulls);
    });
    const client = new GitCodeClient({
      token: 'token-1',
      baseUrl: 'https://api.example.test/api/v5',
      fetchImpl
    });

    const pulls = await client.listOpenPulls({ owner: 'o', repo: 'r' });

    expect(pulls).toHaveLength(101);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(fetchImpl.mock.calls[0][0].searchParams.get('state')).toBe('open');
    expect(fetchImpl.mock.calls[0][0].searchParams.get('per_page')).toBe('100');
    expect(fetchImpl.mock.calls[0][0].searchParams.get('access_token')).toBe('token-1');
  });

  it('fetches all open issue pages', async () => {
    const fetchImpl = vi.fn(async (url) => {
      const page = Number(url.searchParams.get('page'));
      const issues = page === 1
        ? [{ number: 1 }, { number: 2 }]
        : [];

      return Response.json(issues);
    });
    const client = new GitCodeClient({
      token: 'token-1',
      baseUrl: 'https://api.example.test/api/v5',
      fetchImpl
    });

    const issues = await client.listOpenIssues({ owner: 'o', repo: 'r' });

    expect(issues).toHaveLength(2);
    expect(fetchImpl.mock.calls[0][0].pathname).toContain('/repos/o/r/issues');
    expect(fetchImpl.mock.calls[0][0].searchParams.get('state')).toBe('open');
  });
});
