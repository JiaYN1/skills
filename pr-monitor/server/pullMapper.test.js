import { describe, expect, it } from 'vitest';
import { normalizePull } from './pullMapper.js';

describe('normalizePull', () => {
  const repo = { owner: 'o', repo: 'r', label: 'Repo' };
  const refreshedAt = '2026-04-29T06:00:00.000Z';

  it('uses a dash when commits are empty', () => {
    const row = normalizePull(repo, {
      number: 7,
      title: 'No commits',
      user: { login: 'alice' },
      created_at: '2026-04-28T06:00:00.000Z',
      updated_at: '2026-04-28T07:00:00.000Z'
    }, [], refreshedAt);

    expect(row.lastModifier).toBe('-');
  });

  it('selects the author from the latest commit by timestamp', () => {
    const row = normalizePull(repo, {
      number: 8,
      title: 'With commits',
      user: { login: 'alice' },
      created_at: '2026-04-28T06:00:00.000Z'
    }, [
      {
        author: { login: 'old-author' },
        commit: { author: { date: '2026-04-28T06:30:00.000Z' } }
      },
      {
        author: { login: 'new-author' },
        commit: { committer: { date: '2026-04-28T08:30:00.000Z' } }
      }
    ], refreshedAt);

    expect(row.lastModifier).toBe('new-author');
  });
});
