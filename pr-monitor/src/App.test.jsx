// @vitest-environment jsdom
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from './App.jsx';

const pulls = [
  {
    repoOwner: 'o',
    repoName: 'r',
    repoLabel: 'Repo',
    number: 1,
    title: 'Fresh PR',
    state: 'open',
    author: 'alice',
    lastModifier: 'bob',
    createdAt: '2026-04-28T00:00:00.000Z',
    updatedAt: '2026-04-28T01:00:00.000Z',
    htmlUrl: 'https://gitcode.com/o/r/pull/1',
    ageDaysExact: 1,
    isStale: false
  },
  {
    repoOwner: 'o',
    repoName: 'r',
    repoLabel: 'Repo',
    number: 2,
    title: 'Old PR',
    state: 'open',
    author: 'carol',
    lastModifier: 'dave',
    createdAt: '2026-04-20T00:00:00.000Z',
    updatedAt: '2026-04-21T01:00:00.000Z',
    htmlUrl: 'https://gitcode.com/o/r/pull/2',
    ageDaysExact: 9,
    isStale: true
  }
];

const status = {
  isRefreshing: false,
  lastSuccessAt: '2026-04-29T06:00:00.000Z',
  lastError: null,
  staleDays: 5,
  repos: [
    {
      repoKey: 'o/r',
      owner: 'o',
      repo: 'r',
      label: 'Repo',
      status: 'success',
      lastError: null,
      refreshedAt: '2026-04-29T06:00:00.000Z'
    }
  ]
};

function mockFetch(extra = {}) {
  global.fetch = vi.fn(async (url, options) => {
    if (url === '/api/pulls') {
      return Response.json(pulls);
    }

    if (url === '/api/status') {
      return Response.json(status);
    }

    if (url === '/api/refresh' && options?.method === 'POST') {
      if (extra.refreshDelay) {
        await new Promise((resolve) => setTimeout(resolve, extra.refreshDelay));
      }

      return Response.json({ ok: true, result: { ok: true }, status });
    }

    return Response.json({ error: 'not found' }, { status: 404 });
  });
}

describe('App', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('renders open PRs and filters PRs older than the stale threshold', async () => {
    mockFetch();
    render(<App />);

    expect(await screen.findByText('Fresh PR')).toBeInTheDocument();
    expect(screen.getByText('Old PR')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('仅显示大于 5 天的 PR'));

    expect(screen.queryByText('Fresh PR')).not.toBeInTheDocument();
    expect(screen.getByText('Old PR')).toBeInTheDocument();
  });

  it('disables the manual refresh button while refresh is running', async () => {
    mockFetch({ refreshDelay: 50 });
    render(<App />);

    const button = await screen.findByRole('button', { name: '刷新' });
    fireEvent.click(button);

    expect(button).toBeDisabled();
    expect(screen.getByRole('button', { name: '刷新中' })).toBeDisabled();

    await vi.advanceTimersByTimeAsync(60);

    await waitFor(() => expect(screen.getByRole('button', { name: '刷新' })).not.toBeDisabled());
  });
});
