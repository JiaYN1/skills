import { describe, expect, it } from 'vitest';
import { getAgeInDays, isOlderThanDays } from './age.js';

describe('server age helpers', () => {
  const now = new Date('2026-04-29T06:00:00.000Z');

  it('treats exactly 5 days as not stale', () => {
    const createdAt = new Date(now.getTime() - 5 * 24 * 60 * 60 * 1000).toISOString();

    expect(getAgeInDays(createdAt, now)).toBe(5);
    expect(isOlderThanDays(createdAt, 5, now)).toBe(false);
  });

  it('treats more than 5 days as stale', () => {
    const createdAt = new Date(now.getTime() - (5 * 24 * 60 * 60 * 1000 + 1)).toISOString();

    expect(isOlderThanDays(createdAt, 5, now)).toBe(true);
  });
});
