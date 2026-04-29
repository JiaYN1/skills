export const MS_PER_DAY = 24 * 60 * 60 * 1000;
export const SHANGHAI_TIME_ZONE = 'Asia/Shanghai';

export function getAgeInDays(createdAt, now = new Date()) {
  const createdTime = new Date(createdAt).getTime();
  const nowTime = new Date(now).getTime();

  if (!Number.isFinite(createdTime) || !Number.isFinite(nowTime)) {
    return 0;
  }

  return Math.max(0, (nowTime - createdTime) / MS_PER_DAY);
}

export function isStaleByCreatedAt(createdAt, staleDays, now = new Date()) {
  return getAgeInDays(createdAt, now) > Number(staleDays);
}

export function formatDateTime(value) {
  if (!value) {
    return '-';
  }

  const date = new Date(value);

  if (!Number.isFinite(date.getTime())) {
    return '-';
  }

  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: SHANGHAI_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false
  }).format(date);
}

export function formatOpenDuration(ageDaysExact) {
  const days = Number(ageDaysExact);

  if (!Number.isFinite(days) || days < 0) {
    return '-';
  }

  if (days < 1) {
    const hours = Math.floor(days * 24);
    return `${hours} 小时`;
  }

  return `${Math.floor(days)} 天`;
}
