import cron from 'node-cron';
import { loadConfig } from './config.js';
import { initDb } from './db.js';
import { GitCodeClient } from './gitcodeClient.js';
import { createRefreshService, RefreshAlreadyRunningError } from './refreshService.js';
import { createServer, resolveStaticDir } from './app.js';

const config = loadConfig();
const db = initDb(config.dbPath);
const client = new GitCodeClient({
  token: config.gitcodeToken,
  baseUrl: config.gitcodeApiBaseUrl
});
const refreshService = createRefreshService({
  db,
  client,
  repos: config.repos
});

const app = await createServer({
  db,
  refreshService,
  repos: config.repos,
  staleDays: config.staleDays,
  issueThresholds: config.issueThresholds,
  staticDir: resolveStaticDir(),
  logger: {
    level: process.env.LOG_LEVEL || 'info'
  }
});

cron.schedule(
  '0 6 * * *',
  () => {
    refreshService.refreshAll().catch((error) => {
      if (!(error instanceof RefreshAlreadyRunningError)) {
        app.log.error(error, 'Scheduled refresh failed');
      }
    });
  },
  {
    timezone: config.timezone
  }
);

if (config.refreshOnStart) {
  setTimeout(() => {
    refreshService.refreshAll().catch((error) => {
      if (!(error instanceof RefreshAlreadyRunningError)) {
        app.log.error(error, 'Startup refresh failed');
      }
    });
  }, 0);
}

const address = await app.listen({
  port: config.port,
  host: '0.0.0.0'
});

app.log.info(`GitCode PR & Issue Monitor listening at ${address}`);

async function shutdown() {
  await app.close();
  db.close();
}

process.on('SIGTERM', () => {
  shutdown().finally(() => process.exit(0));
});

process.on('SIGINT', () => {
  shutdown().finally(() => process.exit(0));
});
