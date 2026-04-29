export const MS_PER_DAY = 24 * 60 * 60 * 1000;

export function getAgeInDays(createdAt, now = new Date()) {
  const createdTime = new Date(createdAt).getTime();
  const nowTime = new Date(now).getTime();

  if (!Number.isFinite(createdTime) || !Number.isFinite(nowTime)) {
    return 0;
  }

  return Math.max(0, (nowTime - createdTime) / MS_PER_DAY);
}

export function getWholeAgeDays(createdAt, now = new Date()) {
  return Math.floor(getAgeInDays(createdAt, now));
}

export function isOlderThanDays(createdAt, staleDays, now = new Date()) {
  return getAgeInDays(createdAt, now) > Number(staleDays);
}
