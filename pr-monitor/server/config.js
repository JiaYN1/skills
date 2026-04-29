import fs from 'node:fs';
import path from 'node:path';
import dotenv from 'dotenv';

dotenv.config({ quiet: true });

export const DEFAULT_TIMEZONE = 'Asia/Shanghai';
export const DEFAULT_GITCODE_API_BASE_URL = 'https://api.gitcode.com/api/v5';

function parsePositiveInteger(value, fallback) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function parseBoolean(value, fallback) {
  if (value === undefined) {
    return fallback;
  }

  return ['1', 'true', 'yes', 'on'].includes(String(value).toLowerCase());
}

function normalizeRepo(entry, index) {
  if (!entry || typeof entry !== 'object') {
    throw new Error(`Repo entry at index ${index} must be an object.`);
  }

  const owner = String(entry.owner || '').trim();
  const repo = String(entry.repo || '').trim();

  if (!owner || !repo) {
    throw new Error(`Repo entry at index ${index} must include owner and repo.`);
  }

  return {
    owner,
    repo,
    label: String(entry.label || `${owner}/${repo}`).trim()
  };
}

export function loadRepos(reposConfigPath) {
  if (!fs.existsSync(reposConfigPath)) {
    return [];
  }

  const raw = fs.readFileSync(reposConfigPath, 'utf8');
  const parsed = JSON.parse(raw || '[]');

  if (!Array.isArray(parsed)) {
    throw new Error(`${reposConfigPath} must contain a JSON array.`);
  }

  return parsed.map(normalizeRepo);
}

export function loadConfig(env = process.env, cwd = process.cwd()) {
  const timezone = env.TZ || DEFAULT_TIMEZONE;
  env.TZ = timezone;

  const dataDir = env.DATA_DIR || path.join(cwd, 'data');
  const reposConfigPath = env.REPOS_CONFIG_PATH || path.join(cwd, 'config', 'repos.json');

  return {
    port: parsePositiveInteger(env.PORT, 3000),
    staleDays: parsePositiveInteger(env.STALE_DAYS, 5),
    issueThresholds: {
      bugDays: parsePositiveInteger(env.BUG_ISSUE_STALE_DAYS, 5),
      featureDays: parsePositiveInteger(env.FEATURE_ISSUE_STALE_DAYS, 30)
    },
    timezone,
    dataDir,
    dbPath: env.DB_PATH || path.join(dataDir, 'pr-monitor.sqlite'),
    reposConfigPath,
    repos: loadRepos(reposConfigPath),
    gitcodeToken: env.GITCODE_TOKEN || '',
    gitcodeApiBaseUrl: env.GITCODE_API_BASE_URL || DEFAULT_GITCODE_API_BASE_URL,
    refreshOnStart: parseBoolean(env.REFRESH_ON_START, true)
  };
}
