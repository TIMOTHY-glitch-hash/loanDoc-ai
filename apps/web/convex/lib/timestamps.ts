/**
 * Timestamp helpers.
 *
 * Every write goes through one of these so `createdAt`/`updatedAt` can never be
 * forgotten or set inconsistently across mutations.
 */

export function withTimestamps<T extends object>(
  doc: T,
): T & { createdAt: number; updatedAt: number } {
  const now = Date.now();
  return { ...doc, createdAt: now, updatedAt: now };
}

export function touch<T extends object>(patch: T): T & { updatedAt: number } {
  return { ...patch, updatedAt: Date.now() };
}
