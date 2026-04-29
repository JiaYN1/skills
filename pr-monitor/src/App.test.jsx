// @vitest-environment jsdom
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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

const issues = [
  {
    repoOwner: 'o',
    repoName: 'r',
    repoLabel: 'Repo',
    number: 8,
    title: '[Bug] Old issue',
    state: 'open',
    author: 'erin',
    createdAt: '2026-04-20T00:00:00.000Z',
    updatedAt: '2026-04-21T01:00:00.000Z',
    htmlUrl: 'https://gitcode.com/o/r/issues/8',
    labels: ['bug'],
    categories: ['bug'],
    ageDaysExact: 9,
    isBug: true,
    isFeature: false,
    isBugStale: true,
    isFeatureStale: false,
    needsAttention: true
  },
  {
    repoOwner: 'o',
    repoName: 'r',
    repoLabel: 'Repo',
    number: 9,
    title: '[Feature] Fresh issue',
    state: 'open',
    author: 'frank',
    createdAt: '2026-04-28T00:00:00.000Z',
    updatedAt: '2026-04-28T01:00:00.000Z',
    htmlUrl: 'https://gitcode.com/o/r/issues/9',
    labels: ['feature'],
    categories: ['feature'],
    ageDaysExact: 1,
    isBug: false,
    isFeature: true,
    isBugStale: false,
    isFeatureStale: false,
    needsAttention: false
  }
];

const status = {
  isRefreshing: false,
  lastSuccessAt: '2026-04-29T06:00:00.000Z',
  lastError: null,
  staleDays: 5,
  issueThresholds: {
    bugDays: 5,
    featureDays: 30
  },
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

    if (url === '/api/issues') {
      return Response.json(issues);
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

  it('switches to issues and filters old bug issues', async () => {
    mockFetch();
    render(<App />);

    expect(await screen.findByText('Fresh PR')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Issue' }));

    expect(await screen.findByText('[Bug] Old issue')).toBeInTheDocument();
    expect(screen.getByText('[Feature] Fresh issue')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Bug > 5 天' }));

    expect(screen.getByText('[Bug] Old issue')).toBeInTheDocument();
    expect(screen.queryByText('[Feature] Fresh issue')).not.toBeInTheDocument();
  });

  it('disables the manual refresh button while refresh is running', async () => {
    mockFetch({ refreshDelay: 50 });
    render(<App />);

    const button = await screen.findByRole('button', { name: '刷新' });
    await act(async () => {
      fireEvent.click(button);
    });

    expect(button).toBeDisabled();
    expect(screen.getByRole('button', { name: '刷新中' })).toBeDisabled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60);
    });

    await waitFor(() => expect(screen.getByRole('button', { name: '刷新' })).not.toBeDisabled());
  });
});
