import { describe, expect, it } from 'vitest';
import { getAgeInDays, getIssueAttentionState, isStaleByCreatedAt, normalizeIssueThresholds } from './utils.js';

describe('PR age helpers', () => {
  const now = new Date('2026-04-29T06:00:00.000Z');

  it('does not mark a PR stale at exactly 5 days', () => {
    const createdAt = new Date(now.getTime() - 5 * 24 * 60 * 60 * 1000).toISOString();

    expect(getAgeInDays(createdAt, now)).toBe(5);
    expect(isStaleByCreatedAt(createdAt, 5, now)).toBe(false);
  });

  it('does not mark a PR stale under 5 days', () => {
    const createdAt = new Date(now.getTime() - (5 * 24 * 60 * 60 * 1000 - 1)).toISOString();

    expect(isStaleByCreatedAt(createdAt, 5, now)).toBe(false);
  });

  it('marks a PR stale only after more than 5 days', () => {
    const createdAt = new Date(now.getTime() - (5 * 24 * 60 * 60 * 1000 + 1)).toISOString();

    expect(isStaleByCreatedAt(createdAt, 5, now)).toBe(true);
  });

  it('applies default thresholds for issue attention filters', () => {
    expect(normalizeIssueThresholds({})).toEqual({ bugDays: 5, featureDays: 30 });
  });

  it('marks bug issues stale after 5 days and feature issues stale after 30 days', () => {
    const oldBug = getIssueAttentionState({
      title: '[Bug] Broken flow',
      createdAt: '2026-04-20T00:00:00.000Z'
    }, { bugDays: 5, featureDays: 30 }, now);
    const oldFeature = getIssueAttentionState({
      title: '[Feature] Add new flow',
      createdAt: '2026-03-20T00:00:00.000Z'
    }, { bugDays: 5, featureDays: 30 }, now);

    expect(oldBug.isBugStale).toBe(true);
    expect(oldFeature.isFeatureStale).toBe(true);
  });
});
