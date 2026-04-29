import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

export function getRepoKey(repo) {
  return `${repo.owner}/${repo.repo}`;
}

function ensureParentDirectory(dbPath) {
  if (dbPath === ':memory:') {
    return;
  }

  fs.mkdirSync(path.dirname(dbPath), { recursive: true });
}

function withTransaction(db, callback) {
  db.exec('BEGIN IMMEDIATE');

  try {
    const result = callback();
    db.exec('COMMIT');
    return result;
  } catch (error) {
    db.exec('ROLLBACK');
    throw error;
  }
}

export function initDb(dbPath) {
  ensureParentDirectory(dbPath);

  const sqlite = new DatabaseSync(dbPath);
  sqlite.exec('PRAGMA foreign_keys = ON');

  if (dbPath !== ':memory:') {
    sqlite.exec('PRAGMA journal_mode = WAL');
  }

  sqlite.exec(`
    CREATE TABLE IF NOT EXISTS pulls (
      repo_owner TEXT NOT NULL,
      repo_name TEXT NOT NULL,
      repo_label TEXT NOT NULL,
      number INTEGER NOT NULL,
      title TEXT NOT NULL,
      state TEXT NOT NULL,
      author TEXT NOT NULL,
      last_modifier TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT,
      html_url TEXT NOT NULL,
      refreshed_at TEXT NOT NULL,
      PRIMARY KEY (repo_owner, repo_name, number)
    );

    CREATE TABLE IF NOT EXISTS issues (
      repo_owner TEXT NOT NULL,
      repo_name TEXT NOT NULL,
      repo_label TEXT NOT NULL,
      number INTEGER NOT NULL,
      title TEXT NOT NULL,
      state TEXT NOT NULL,
      author TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT,
      html_url TEXT NOT NULL,
      issue_type TEXT NOT NULL,
      labels_json TEXT NOT NULL,
      is_bug INTEGER NOT NULL DEFAULT 0,
      is_feature INTEGER NOT NULL DEFAULT 0,
      refreshed_at TEXT NOT NULL,
      PRIMARY KEY (repo_owner, repo_name, number)
    );

    CREATE TABLE IF NOT EXISTS refresh_state (
      id INTEGER PRIMARY KEY CHECK (id = 1),
      is_refreshing INTEGER NOT NULL DEFAULT 0,
      last_started_at TEXT,
      last_finished_at TEXT,
      last_success_at TEXT,
      last_error TEXT
    );

    CREATE TABLE IF NOT EXISTS repo_refresh_state (
      repo_key TEXT PRIMARY KEY,
      repo_owner TEXT NOT NULL,
      repo_name TEXT NOT NULL,
      repo_label TEXT NOT NULL,
      status TEXT NOT NULL,
      last_error TEXT,
      refreshed_at TEXT
    );
  `);

  sqlite.prepare(`
    INSERT OR IGNORE INTO refresh_state (id, is_refreshing)
    VALUES (1, 0)
  `).run();
  sqlite.prepare(`
    UPDATE refresh_state
    SET is_refreshing = 0
    WHERE id = 1
  `).run();

  const insertPull = sqlite.prepare(`
    INSERT INTO pulls (
      repo_owner,
      repo_name,
      repo_label,
      number,
      title,
      state,
      author,
      last_modifier,
      created_at,
      updated_at,
      html_url,
      refreshed_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  const deleteRepoPulls = sqlite.prepare(`
    DELETE FROM pulls WHERE repo_owner = ? AND repo_name = ?
  `);

  const insertIssue = sqlite.prepare(`
    INSERT INTO issues (
      repo_owner,
      repo_name,
      repo_label,
      number,
      title,
      state,
      author,
      created_at,
      updated_at,
      html_url,
      issue_type,
      labels_json,
      is_bug,
      is_feature,
      refreshed_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  const deleteRepoIssues = sqlite.prepare(`
    DELETE FROM issues WHERE repo_owner = ? AND repo_name = ?
  `);

  const upsertRepoState = sqlite.prepare(`
    INSERT INTO repo_refresh_state (
      repo_key,
      repo_owner,
      repo_name,
      repo_label,
      status,
      last_error,
      refreshed_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(repo_key) DO UPDATE SET
      repo_label = excluded.repo_label,
      status = excluded.status,
      last_error = excluded.last_error,
      refreshed_at = excluded.refreshed_at
  `);

  return {
    replaceRepoPulls(repo, rows, refreshedAt) {
      withTransaction(sqlite, () => {
        deleteRepoPulls.run(repo.owner, repo.repo);

        for (const row of rows) {
          insertPull.run(
            repo.owner,
            repo.repo,
            repo.label,
            row.number,
            row.title,
            row.state,
            row.author,
            row.lastModifier,
            row.createdAt,
            row.updatedAt,
            row.htmlUrl,
            refreshedAt
          );
        }
      });
    },

    replaceRepoIssues(repo, rows, refreshedAt) {
      withTransaction(sqlite, () => {
        deleteRepoIssues.run(repo.owner, repo.repo);

        for (const row of rows) {
          insertIssue.run(
            repo.owner,
            repo.repo,
            repo.label,
            row.number,
            row.title,
            row.state,
            row.author,
            row.createdAt,
            row.updatedAt,
            row.htmlUrl,
            row.issueType,
            JSON.stringify(row.labels),
            row.isBug ? 1 : 0,
            row.isFeature ? 1 : 0,
            refreshedAt
          );
        }
      });
    },

    getPulls() {
      return sqlite.prepare(`
        SELECT
          repo_owner,
          repo_name,
          repo_label,
          number,
          title,
          state,
          author,
          last_modifier,
          created_at,
          updated_at,
          html_url,
          refreshed_at
        FROM pulls
        ORDER BY created_at ASC, repo_label ASC, number ASC
      `).all();
    },

    getIssues() {
      return sqlite.prepare(`
        SELECT
          repo_owner,
          repo_name,
          repo_label,
          number,
          title,
          state,
          author,
          created_at,
          updated_at,
          html_url,
          issue_type,
          labels_json,
          is_bug,
          is_feature,
          refreshed_at
        FROM issues
        ORDER BY created_at ASC, repo_label ASC, number ASC
      `).all();
    },

    markRefreshStarted(startedAt) {
      sqlite.prepare(`
        UPDATE refresh_state
        SET is_refreshing = 1,
            last_started_at = ?,
            last_error = NULL
        WHERE id = 1
      `).run(startedAt);
    },

    markRefreshFinished({ finishedAt, lastError }) {
      if (lastError) {
        sqlite.prepare(`
          UPDATE refresh_state
          SET is_refreshing = 0,
              last_finished_at = ?,
              last_error = ?
          WHERE id = 1
        `).run(finishedAt, lastError);
        return;
      }

      sqlite.prepare(`
        UPDATE refresh_state
        SET is_refreshing = 0,
            last_finished_at = ?,
            last_success_at = ?,
            last_error = NULL
        WHERE id = 1
      `).run(finishedAt, finishedAt);
    },

    setRepoStatus(repo, status, lastError, refreshedAt) {
      upsertRepoState.run(
        getRepoKey(repo),
        repo.owner,
        repo.repo,
        repo.label,
        status,
        lastError,
        refreshedAt
      );
    },

    getRefreshStatus() {
      return sqlite.prepare(`
        SELECT
          is_refreshing,
          last_started_at,
          last_finished_at,
          last_success_at,
          last_error
        FROM refresh_state
        WHERE id = 1
      `).get();
    },

    getRepoStatuses() {
      return sqlite.prepare(`
        SELECT
          repo_key,
          repo_owner,
          repo_name,
          repo_label,
          status,
          last_error,
          refreshed_at
        FROM repo_refresh_state
        ORDER BY repo_label ASC
      `).all();
    },

    close() {
      sqlite.close();
    }
  };
}
