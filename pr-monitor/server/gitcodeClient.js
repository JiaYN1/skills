import { DEFAULT_GITCODE_API_BASE_URL } from './config.js';

function encodePathSegment(segment) {
  return encodeURIComponent(segment);
}

function readResponseBody(response) {
  return response.text().then((text) => {
    if (!text) {
      return null;
    }

    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  });
}

export class GitCodeClient {
  constructor({
    token,
    baseUrl = DEFAULT_GITCODE_API_BASE_URL,
    fetchImpl = globalThis.fetch
  } = {}) {
    this.token = token;
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.fetchImpl = fetchImpl;
  }

  async request(pathname, query = {}) {
    if (!this.token) {
      throw new Error('GITCODE_TOKEN is not configured.');
    }

    if (!this.fetchImpl) {
      throw new Error('No fetch implementation is available.');
    }

    const url = new URL(`${this.baseUrl}${pathname}`);

    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null) {
        url.searchParams.set(key, String(value));
      }
    }

    url.searchParams.set('access_token', this.token);

    const response = await this.fetchImpl(url, {
      headers: {
        Accept: 'application/json'
      }
    });
    const body = await readResponseBody(response);

    if (!response.ok) {
      const message = typeof body === 'object' && body?.message
        ? body.message
        : response.statusText;
      throw new Error(`GitCode API ${response.status}: ${message}`);
    }

    return body;
  }

  async listOpenPulls(repo) {
    const pulls = [];
    const perPage = 100;

    for (let page = 1; ; page += 1) {
      const batch = await this.request(
        `/repos/${encodePathSegment(repo.owner)}/${encodePathSegment(repo.repo)}/pulls`,
        {
          state: 'open',
          page,
          per_page: perPage
        }
      );

      if (!Array.isArray(batch)) {
        throw new Error(`GitCode returned a non-array pull list for ${repo.owner}/${repo.repo}.`);
      }

      pulls.push(...batch);

      if (batch.length < perPage) {
        break;
      }
    }

    return pulls;
  }

  async listPullCommits(repo, number) {
    const commits = await this.request(
      `/repos/${encodePathSegment(repo.owner)}/${encodePathSegment(repo.repo)}/pulls/${number}/commits`
    );

    if (!Array.isArray(commits)) {
      throw new Error(`GitCode returned a non-array commit list for ${repo.owner}/${repo.repo}#${number}.`);
    }

    return commits;
  }
}
