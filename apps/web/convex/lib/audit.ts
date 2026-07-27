import type { Id } from '../_generated/dataModel';
import type { MutationCtx } from '../_generated/server';
import { withTimestamps } from './timestamps';

/** Fallback when no policy row is marked active (fresh deployment, or seed data). */
const UNVERSIONED_POLICY = 'unversioned';

/**
 * Append an audit entry, stamping it with the policy version currently in force.
 *
 * Centralised so no mutation can write history without a policy version: without
 * it a past decision cannot be explained, which is the whole point of the log.
 */
export async function recordAudit(
  ctx: MutationCtx,
  entry: {
    applicationId: Id<'loanApplications'>;
    action: string;
    actor: 'AGENT' | 'USER';
    details?: unknown;
  },
): Promise<Id<'auditLogs'>> {
  const activePolicy = await ctx.db
    .query('policies')
    .withIndex('by_active', (q) => q.eq('isActive', true))
    .first();

  return await ctx.db.insert(
    'auditLogs',
    withTimestamps({
      applicationId: entry.applicationId,
      action: entry.action,
      actor: entry.actor,
      details: entry.details ?? null,
      policyVersion: activePolicy?.version ?? UNVERSIONED_POLICY,
      timestamp: Date.now(),
    }),
  );
}
