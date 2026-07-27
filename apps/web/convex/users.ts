import { v } from 'convex/values';
import { mutation, query } from './_generated/server';
import { userRole } from './schema';
import { withTimestamps } from './lib/timestamps';

export const list = query({
  args: { role: v.optional(userRole) },
  handler: async (ctx, args) => {
    if (args.role !== undefined) {
      const role = args.role;
      return await ctx.db
        .query('users')
        .withIndex('by_role', (q) => q.eq('role', role))
        .collect();
    }
    return await ctx.db.query('users').collect();
  },
});

export const getByEmail = query({
  args: { email: v.string() },
  handler: async (ctx, args) =>
    await ctx.db
      .query('users')
      .withIndex('by_email', (q) => q.eq('email', args.email))
      .first(),
});

/**
 * Idempotent by email: Convex cannot enforce uniqueness, so the invariant lives
 * here and callers can safely retry (or re-run the seed) without duplicating.
 */
export const upsert = mutation({
  args: { name: v.string(), email: v.string(), role: userRole },
  handler: async (ctx, args) => {
    const existing = await ctx.db
      .query('users')
      .withIndex('by_email', (q) => q.eq('email', args.email))
      .first();

    if (existing !== null) {
      await ctx.db.patch(existing._id, {
        name: args.name,
        role: args.role,
        updatedAt: Date.now(),
      });
      return existing._id;
    }

    return await ctx.db.insert(
      'users',
      withTimestamps({ name: args.name, email: args.email, role: args.role }),
    );
  },
});
