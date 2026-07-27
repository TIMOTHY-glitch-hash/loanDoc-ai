import { v } from 'convex/values';
import { mutation, query } from './_generated/server';
import { applicationStatus } from './schema';
import { touch, withTimestamps } from './lib/timestamps';
import { recordAudit } from './lib/audit';

/**
 * Review queue. Filtering by status uses the `by_status_and_confidence` index so
 * the lowest-confidence applications - the ones most likely to need a human -
 * come back first without a full table scan.
 */
export const list = query({
  args: {
    status: v.optional(applicationStatus),
    limit: v.optional(v.number()),
  },
  handler: async (ctx, args) => {
    const limit = args.limit ?? 50;

    if (args.status !== undefined) {
      const status = args.status;
      return await ctx.db
        .query('loanApplications')
        .withIndex('by_status_and_confidence', (q) => q.eq('status', status))
        .order('asc')
        .take(limit);
    }

    return await ctx.db.query('loanApplications').order('desc').take(limit);
  },
});

export const get = query({
  args: { id: v.id('loanApplications') },
  handler: async (ctx, args) => await ctx.db.get(args.id),
});

/** An application plus everything needed to render its detail view in one round trip. */
export const getWithRelations = query({
  args: { id: v.id('loanApplications') },
  handler: async (ctx, args) => {
    const application = await ctx.db.get(args.id);
    if (application === null) return null;

    const [documents, auditLogs] = await Promise.all([
      ctx.db
        .query('documents')
        .withIndex('by_application', (q) => q.eq('applicationId', args.id))
        .collect(),
      ctx.db
        .query('auditLogs')
        .withIndex('by_application_and_timestamp', (q) => q.eq('applicationId', args.id))
        .order('desc')
        .take(100),
    ]);

    const officer =
      application.assignedOfficerId === null
        ? null
        : await ctx.db.get(application.assignedOfficerId);

    return { application, documents, auditLogs, officer };
  },
});

export const create = mutation({
  args: {
    applicantName: v.string(),
    loanAmount: v.number(),
    assignedOfficerId: v.optional(v.union(v.id('users'), v.null())),
  },
  handler: async (ctx, args) => {
    const applicationId = await ctx.db.insert(
      'loanApplications',
      withTimestamps({
        applicantName: args.applicantName,
        loanAmount: args.loanAmount,
        // A new application always starts PENDING with no agent opinion yet.
        status: 'PENDING' as const,
        riskFlags: [],
        decisionMemo: null,
        assignedOfficerId: args.assignedOfficerId ?? null,
        agentConfidenceScore: 0,
      }),
    );

    await recordAudit(ctx, {
      applicationId,
      action: 'APPLICATION_CREATED',
      actor: 'USER',
      details: { applicantName: args.applicantName, loanAmount: args.loanAmount },
    });

    return applicationId;
  },
});

/**
 * The only path that changes a decision. Every transition is audited with the
 * policy version in force, so a past decision can be replayed exactly.
 */
export const setStatus = mutation({
  args: {
    id: v.id('loanApplications'),
    status: applicationStatus,
    actor: v.union(v.literal('AGENT'), v.literal('USER')),
    decisionMemo: v.optional(v.string()),
  },
  handler: async (ctx, args) => {
    const application = await ctx.db.get(args.id);
    if (application === null) throw new Error(`Application ${args.id} not found`);

    await ctx.db.patch(
      args.id,
      touch({
        status: args.status,
        ...(args.decisionMemo === undefined ? {} : { decisionMemo: args.decisionMemo }),
      }),
    );

    await recordAudit(ctx, {
      applicationId: args.id,
      action: 'STATUS_CHANGED',
      actor: args.actor,
      details: { from: application.status, to: args.status },
    });
  },
});

/**
 * Persists a generated underwriting memo.
 *
 * Called over Convex's HTTP mutation API by the FastAPI `/generate-memo`
 * endpoint, which is why the arguments are primitives rather than a Doc patch.
 * The memo is written together with the policy version and risk score it was
 * written from: a memo detached from the rules that produced it is a narrative,
 * not a decision record.
 */
export const saveDecisionMemo = mutation({
  args: {
    id: v.id('loanApplications'),
    decisionMemo: v.string(),
    policyVersion: v.string(),
    riskFlags: v.optional(v.array(v.string())),
    agentConfidenceScore: v.optional(v.number()),
  },
  handler: async (ctx, args) => {
    const application = await ctx.db.get(args.id);
    if (application === null) throw new Error(`Application ${args.id} not found`);

    await ctx.db.patch(
      args.id,
      touch({
        decisionMemo: args.decisionMemo,
        ...(args.riskFlags === undefined ? {} : { riskFlags: args.riskFlags }),
        ...(args.agentConfidenceScore === undefined
          ? {}
          : { agentConfidenceScore: args.agentConfidenceScore }),
      }),
    );

    await recordAudit(ctx, {
      applicationId: args.id,
      action: 'DECISION_MEMO_GENERATED',
      actor: 'AGENT',
      details: {
        policyVersion: args.policyVersion,
        characters: args.decisionMemo.length,
      },
    });
  },
});

/** Written by the agent after processing a document bundle. */
export const applyAgentAssessment = mutation({
  args: {
    id: v.id('loanApplications'),
    riskFlags: v.array(v.string()),
    agentConfidenceScore: v.number(),
    decisionMemo: v.union(v.string(), v.null()),
  },
  handler: async (ctx, args) => {
    await ctx.db.patch(
      args.id,
      touch({
        riskFlags: args.riskFlags,
        agentConfidenceScore: args.agentConfidenceScore,
        decisionMemo: args.decisionMemo,
        // Any risk flag forces a human look, regardless of confidence.
        status: args.riskFlags.length > 0 ? ('REVIEW' as const) : ('PENDING' as const),
      }),
    );

    await recordAudit(ctx, {
      applicationId: args.id,
      action: 'AGENT_ASSESSMENT_RECORDED',
      actor: 'AGENT',
      details: { riskFlags: args.riskFlags, agentConfidenceScore: args.agentConfidenceScore },
    });
  },
});
