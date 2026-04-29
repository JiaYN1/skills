import { describe, expect, it } from 'vitest';
import { normalizeIssue } from './issueMapper.js';

describe('normalizeIssue', () => {
  const repo = { owner: 'o', repo: 'r', label: 'Repo' };
  const refreshedAt = '2026-04-29T06:00:00.000Z';

  it('classifies bug issues from labels', () => {
    const row = normalizeIssue(repo, {
      number: '12',
      title: 'Broken flow',
      user: { login: 'alice' },
      labels: [{ name: 'bug' }, { name: 'triaged' }],
      created_at: '2026-04-20T00:00:00.000Z'
    }, refreshedAt);

    expect(row.labels).toEqual(['bug', 'triaged']);
    expect(row.isBug).toBe(true);
    expect(row.isFeature).toBe(false);
  });

  it('classifies feature issues from title when issue type is unreliable', () => {
    const row = normalizeIssue(repo, {
      number: 13,
      title: '[Feature Request] Support new model',
      user: { login: 'bob' },
      issue_type: 'Bug-Report',
      labels: [],
      created_at: '2026-04-20T00:00:00.000Z'
    }, refreshedAt);

    expect(row.isBug).toBe(false);
    expect(row.isFeature).toBe(true);
  });
});
