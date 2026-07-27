import { v } from 'convex/values';
import { mutation, query } from './_generated/server';
import { actor } from './schema';
import { recordAudit } from './lib/audit';

/**
 * Audit logs are append-only by design: there is no update or delete mutation.
 * Anything a user "changes" is a new entry, so history can never be rewritten.
 */
export const listByApplication = query({
  args: {
    applicationId: v.id('loanApplications'),
    limit: v.optional(v.number()),
  },
  handler: async (ctx, args) =>
    await ctx.db
      .query('auditLogs')
      .withIndex('by_application_and_timestamp', (q) => q.eq('applicationId', args.applicationId))
      .order('desc')
      .take(args.limit ?? 100),
});

/** Explicit entry point for actions that are not already audited by a mutation. */
export const append = mutation({
  args: {
    applicationId: v.id('loanApplications'),
    action: v.string(),
    actor,
    details: v.optional(v.any()),
  },
  handler: async (ctx, args) =>
    await recordAudit(ctx, {
      applicationId: args.applicationId,
      action: args.action,
      actor: args.actor,
      details: args.details ?? null,
    }),
});
