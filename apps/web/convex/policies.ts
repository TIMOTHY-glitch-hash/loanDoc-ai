import { v } from 'convex/values';
import { mutation, query } from './_generated/server';
import { touch, withTimestamps } from './lib/timestamps';

export const listAll = query({
  args: {},
  handler: async (ctx) => await ctx.db.query('policies').order('desc').collect(),
});

/** The version every new audit entry is stamped with. */
export const getActive = query({
  args: {},
  handler: async (ctx) =>
    await ctx.db
      .query('policies')
      .withIndex('by_active', (q) => q.eq('isActive', true))
      .first(),
});

/**
 * Publish a policy version.
 *
 * Convex has no unique constraints, so the "exactly one active version" invariant
 * is enforced here: previously active rows are deactivated in the same
 * transaction, making the swap atomic.
 */
export const publish = mutation({
  args: {
    name: v.string(),
    version: v.string(),
    rules: v.array(v.any()),
    description: v.string(),
  },
  handler: async (ctx, args) => {
    const active = await ctx.db
      .query('policies')
      .withIndex('by_active', (q) => q.eq('isActive', true))
      .collect();

    for (const policy of active) {
      await ctx.db.patch(policy._id, touch({ isActive: false }));
    }

    const existing = await ctx.db
      .query('policies')
      .withIndex('by_name_and_version', (q) => q.eq('name', args.name).eq('version', args.version))
      .first();

    // Re-publishing a known version reactivates it rather than duplicating the
    // row, so `auditLogs.policyVersion` keeps pointing at a single definition.
    if (existing !== null) {
      await ctx.db.patch(
        existing._id,
        touch({ isActive: true, rules: args.rules, description: args.description }),
      );
      return existing._id;
    }

    return await ctx.db.insert(
      'policies',
      withTimestamps({
        name: args.name,
        version: args.version,
        rules: args.rules,
        isActive: true,
        description: args.description,
      }),
    );
  },
});
