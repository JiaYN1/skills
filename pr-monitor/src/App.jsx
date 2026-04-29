import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, ExternalLink, RefreshCw } from 'lucide-react';
import {
  DEFAULT_ISSUE_THRESHOLDS,
  formatDateTime,
  formatOpenDuration,
  getIssueAttentionState,
  isStaleByCreatedAt,
  normalizeIssueThresholds
} from './utils.js';

const PULL_VIEW = 'pulls';
const ISSUE_VIEW = 'issues';
const ISSUE_FILTER_ALL = 'all';
const ISSUE_FILTER_BUG_STALE = 'bug-stale';
const ISSUE_FILTER_FEATURE_STALE = 'feature-stale';

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(body.error || body.message || `Request failed with ${response.status}`);
  }

  return body;
}

function statusLabel(status) {
  if (status === 'success') {
    return '正常';
  }

  if (status === 'error') {
    return '失败';
  }

  return '等待';
}

function issueCategories(issue) {
  const state = getIssueAttentionState(issue, DEFAULT_ISSUE_THRESHOLDS);
  const categories = [];

  if (state.isBug) {
    categories.push('Bug');
  }

  if (state.isFeature) {
    categories.push('Feature');
  }

  return categories;
}

function issueMatchesFilter(issue, filter, issueThresholds) {
  const state = getIssueAttentionState(issue, issueThresholds);

  if (filter === ISSUE_FILTER_BUG_STALE) {
    return state.isBugStale;
  }

  if (filter === ISSUE_FILTER_FEATURE_STALE) {
    return state.isFeatureStale;
  }

  return true;
}

function App() {
  const [pulls, setPulls] = useState([]);
  const [issues, setIssues] = useState([]);
  const [status, setStatus] = useState(null);
  const [view, setView] = useState(PULL_VIEW);
  const [onlyStale, setOnlyStale] = useState(false);
  const [issueFilter, setIssueFilter] = useState(ISSUE_FILTER_ALL);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  const staleDays = status?.staleDays ?? 5;
  const issueThresholds = normalizeIssueThresholds(status?.issueThresholds);
  const isBusy = refreshing || Boolean(status?.isRefreshing);

  async function loadData({ quiet = false } = {}) {
    if (!quiet) {
      setLoading(true);
    }

    try {
      const [pullData, issueData, statusData] = await Promise.all([
        fetchJson('/api/pulls'),
        fetchJson('/api/issues'),
        fetchJson('/api/status')
      ]);
      setPulls(pullData);
      setIssues(issueData);
      setStatus(statusData);
      setError('');
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : String(loadError));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadData();
    const intervalId = window.setInterval(() => {
      loadData({ quiet: true });
    }, 30000);

    return () => window.clearInterval(intervalId);
  }, []);

  async function handleRefresh() {
    setRefreshing(true);
    setError('');

    try {
      await fetchJson('/api/refresh', { method: 'POST' });
      await loadData({ quiet: true });
    } catch (refreshError) {
      setError(refreshError instanceof Error ? refreshError.message : String(refreshError));
      await loadData({ quiet: true });
    } finally {
      setRefreshing(false);
    }
  }

  const visiblePulls = useMemo(() => {
    const filtered = onlyStale
      ? pulls.filter((pull) => pull.isStale ?? isStaleByCreatedAt(pull.createdAt, staleDays))
      : pulls;

    return [...filtered].sort((a, b) => {
      if (a.isStale !== b.isStale) {
        return a.isStale ? -1 : 1;
      }

      return new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime();
    });
  }, [onlyStale, pulls, staleDays]);

  const visibleIssues = useMemo(() => {
    const filtered = issues.filter((issue) => issueMatchesFilter(issue, issueFilter, issueThresholds));

    return [...filtered].sort((a, b) => {
      const aAttention = getIssueAttentionState(a, issueThresholds).needsAttention;
      const bAttention = getIssueAttentionState(b, issueThresholds).needsAttention;

      if (aAttention !== bAttention) {
        return aAttention ? -1 : 1;
      }

      return new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime();
    });
  }, [issueFilter, issueThresholds, issues]);

  const staleCount = pulls.filter((pull) => pull.isStale).length;
  const bugStaleCount = issues.filter((issue) => getIssueAttentionState(issue, issueThresholds).isBugStale).length;
  const featureStaleCount = issues.filter((issue) => getIssueAttentionState(issue, issueThresholds).isFeatureStale).length;
  const repoErrors = status?.repos?.filter((repo) => repo.lastError) ?? [];

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">GitCode</p>
          <h1>PR &amp; Issue Monitor</h1>
        </div>
        <button
          className="refresh-button"
          type="button"
          onClick={handleRefresh}
          disabled={isBusy}
        >
          <RefreshCw className={isBusy ? 'spin' : ''} size={18} aria-hidden="true" />
          <span>{isBusy ? '刷新中' : '刷新'}</span>
        </button>
      </header>

      <section className="view-switch" aria-label="监控类型">
        <button
          className="view-switch-button"
          data-active={view === PULL_VIEW}
          type="button"
          onClick={() => setView(PULL_VIEW)}
        >
          PR
        </button>
        <button
          className="view-switch-button"
          data-active={view === ISSUE_VIEW}
          type="button"
          onClick={() => setView(ISSUE_VIEW)}
        >
          Issue
        </button>
      </section>

      <section className="status-strip" aria-label="刷新状态">
        {view === PULL_VIEW ? (
          <>
            <div>
              <span className="metric-label">Open PR</span>
              <strong>{pulls.length}</strong>
            </div>
            <div>
              <span className="metric-label">大于 {staleDays} 天</span>
              <strong>{staleCount}</strong>
            </div>
            <div>
              <span className="metric-label">最近成功刷新</span>
              <strong>{formatDateTime(status?.lastSuccessAt)}</strong>
            </div>
            <div>
              <span className="metric-label">刷新状态</span>
              <strong>{isBusy ? '运行中' : '空闲'}</strong>
            </div>
          </>
        ) : (
          <>
            <div>
              <span className="metric-label">Open Issue</span>
              <strong>{issues.length}</strong>
            </div>
            <div>
              <span className="metric-label">Bug &gt; {issueThresholds.bugDays} 天</span>
              <strong>{bugStaleCount}</strong>
            </div>
            <div>
              <span className="metric-label">Feature &gt; {issueThresholds.featureDays} 天</span>
              <strong>{featureStaleCount}</strong>
            </div>
            <div>
              <span className="metric-label">最近成功刷新</span>
              <strong>{formatDateTime(status?.lastSuccessAt)}</strong>
            </div>
          </>
        )}
      </section>

      {(error || status?.lastError || repoErrors.length > 0) && (
        <section className="alert" role="alert">
          <AlertTriangle size={18} aria-hidden="true" />
          <div>
            {error && <p>{error}</p>}
            {!error && status?.lastError && <p>{status.lastError}</p>}
            {repoErrors.map((repo) => (
              <p key={repo.repoKey}>{repo.label}: {repo.lastError}</p>
            ))}
          </div>
        </section>
      )}

      <section className="toolbar" aria-label="筛选">
        {view === PULL_VIEW ? (
          <>
            <label className="switch">
              <input
                type="checkbox"
                checked={onlyStale}
                onChange={(event) => setOnlyStale(event.target.checked)}
                aria-label={`仅显示大于 ${staleDays} 天的 PR`}
              />
              <span className="switch-track" aria-hidden="true">
                <span className="switch-thumb" />
              </span>
              <span>仅显示大于 {staleDays} 天</span>
            </label>
            <span className="toolbar-count">显示 {visiblePulls.length} / {pulls.length}</span>
          </>
        ) : (
          <>
            <div className="segmented-control" role="group" aria-label="Issue 类型筛选">
              <button
                className="segmented-control-button"
                data-active={issueFilter === ISSUE_FILTER_ALL}
                type="button"
                onClick={() => setIssueFilter(ISSUE_FILTER_ALL)}
              >
                全部
              </button>
              <button
                className="segmented-control-button"
                data-active={issueFilter === ISSUE_FILTER_BUG_STALE}
                type="button"
                onClick={() => setIssueFilter(ISSUE_FILTER_BUG_STALE)}
              >
                Bug &gt; {issueThresholds.bugDays} 天
              </button>
              <button
                className="segmented-control-button"
                data-active={issueFilter === ISSUE_FILTER_FEATURE_STALE}
                type="button"
                onClick={() => setIssueFilter(ISSUE_FILTER_FEATURE_STALE)}
              >
                Feature &gt; {issueThresholds.featureDays} 天
              </button>
            </div>
            <span className="toolbar-count">显示 {visibleIssues.length} / {issues.length}</span>
          </>
        )}
      </section>

      <section className="repo-state" aria-label="仓库状态">
        {(status?.repos ?? []).map((repo) => (
          <div className="repo-chip" data-status={repo.status} key={repo.repoKey}>
            <span>{repo.label}</span>
            <strong>{statusLabel(repo.status)}</strong>
          </div>
        ))}
      </section>

      {view === PULL_VIEW ? (
        <section className="table-wrap" aria-label="Open PR 列表">
          <table>
            <thead>
              <tr>
                <th>仓库</th>
                <th>PR</th>
                <th>创建者</th>
                <th>最近修改者</th>
                <th>创建时间</th>
                <th>已打开</th>
                <th>更新时间</th>
                <th>状态链接</th>
              </tr>
            </thead>
            <tbody>
              {visiblePulls.map((pull) => (
                <tr className={pull.isStale ? 'stale-row' : ''} key={`${pull.repoOwner}/${pull.repoName}#${pull.number}`}>
                  <td>
                    <span className="repo-name">{pull.repoLabel}</span>
                  </td>
                  <td className="title-cell">
                    <a href={pull.htmlUrl} target="_blank" rel="noreferrer">
                      <span className="pr-number">#{pull.number}</span>
                      {pull.title}
                    </a>
                  </td>
                  <td>{pull.author}</td>
                  <td>{pull.lastModifier}</td>
                  <td>{formatDateTime(pull.createdAt)}</td>
                  <td>
                    <span className={pull.isStale ? 'age-pill stale' : 'age-pill'}>
                      {formatOpenDuration(pull.ageDaysExact)}
                    </span>
                  </td>
                  <td>{formatDateTime(pull.updatedAt)}</td>
                  <td>
                    <a className="open-link" href={pull.htmlUrl} target="_blank" rel="noreferrer">
                      Open
                      <ExternalLink size={14} aria-hidden="true" />
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {!loading && visiblePulls.length === 0 && (
            <div className="empty-state">
              {onlyStale ? '没有大于阈值的 open PR' : '没有缓存的 open PR'}
            </div>
          )}

          {loading && <div className="empty-state">加载中</div>}
        </section>
      ) : (
        <section className="table-wrap" aria-label="Open Issue 列表">
          <table>
            <thead>
              <tr>
                <th>仓库</th>
                <th>Issue</th>
                <th>类型</th>
                <th>标签</th>
                <th>创建者</th>
                <th>创建时间</th>
                <th>已打开</th>
                <th>更新时间</th>
                <th>状态链接</th>
              </tr>
            </thead>
            <tbody>
              {visibleIssues.map((issue) => {
                const attentionState = getIssueAttentionState(issue, issueThresholds);
                const categories = issueCategories(issue);

                return (
                  <tr className={attentionState.needsAttention ? 'stale-row' : ''} key={`${issue.repoOwner}/${issue.repoName}#${issue.number}`}>
                    <td>
                      <span className="repo-name">{issue.repoLabel}</span>
                    </td>
                    <td className="title-cell">
                      <a href={issue.htmlUrl} target="_blank" rel="noreferrer">
                        <span className="pr-number">#{issue.number}</span>
                        {issue.title}
                      </a>
                    </td>
                    <td>
                      <div className="badge-group">
                        {categories.map((category) => (
                          <span
                            className={`tag-badge ${category === 'Bug' ? 'tag-badge-bug' : 'tag-badge-feature'}`}
                            key={category}
                          >
                            {category}
                          </span>
                        ))}
                        {categories.length === 0 && <span>{issue.issueType || '-'}</span>}
                      </div>
                    </td>
                    <td>
                      <div className="badge-group">
                        {(issue.labels ?? []).map((label) => (
                          <span className="tag-badge" key={label}>{label}</span>
                        ))}
                        {(issue.labels ?? []).length === 0 && <span>-</span>}
                      </div>
                    </td>
                    <td>{issue.author}</td>
                    <td>{formatDateTime(issue.createdAt)}</td>
                    <td>
                      <span className={attentionState.needsAttention ? 'age-pill stale' : 'age-pill'}>
                        {formatOpenDuration(issue.ageDaysExact)}
                      </span>
                    </td>
                    <td>{formatDateTime(issue.updatedAt)}</td>
                    <td>
                      <a className="open-link" href={issue.htmlUrl} target="_blank" rel="noreferrer">
                        Open
                        <ExternalLink size={14} aria-hidden="true" />
                      </a>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {!loading && visibleIssues.length === 0 && (
            <div className="empty-state">
              {issueFilter === ISSUE_FILTER_ALL ? '没有缓存的 open issue' : '没有符合筛选条件的 issue'}
            </div>
          )}

          {loading && <div className="empty-state">加载中</div>}
        </section>
      )}
    </main>
  );
}

export default App;
