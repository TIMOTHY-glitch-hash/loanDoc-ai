import { v } from 'convex/values';
import { mutation, query } from './_generated/server';
import { extractionStatus } from './schema';
import { touch, withTimestamps } from './lib/timestamps';
import { recordAudit } from './lib/audit';

export const listByApplication = query({
  args: { applicationId: v.id('loanApplications') },
  handler: async (ctx, args) =>
    await ctx.db
      .query('documents')
      .withIndex('by_application', (q) => q.eq('applicationId', args.applicationId))
      .collect(),
});

/** Worker queue: everything still waiting on the extraction pipeline. */
export const listPending = query({
  args: { limit: v.optional(v.number()) },
  handler: async (ctx, args) =>
    await ctx.db
      .query('documents')
      .withIndex('by_extraction_status', (q) => q.eq('extractionStatus', 'PENDING'))
      .take(args.limit ?? 25),
});

export const create = mutation({
  args: {
    applicationId: v.id('loanApplications'),
    fileUrl: v.string(),
    fileName: v.string(),
    fileType: v.string(),
  },
  handler: async (ctx, args) => {
    const documentId = await ctx.db.insert(
      'documents',
      withTimestamps({
        applicationId: args.applicationId,
        fileUrl: args.fileUrl,
        fileName: args.fileName,
        fileType: args.fileType,
        extractionStatus: 'PENDING' as const,
        extractedData: null,
      }),
    );

    await recordAudit(ctx, {
      applicationId: args.applicationId,
      action: 'DOCUMENT_UPLOADED',
      actor: 'USER',
      details: { documentId, fileName: args.fileName },
    });

    return documentId;
  },
});

/**
 * Single write path for the pipeline. Callers pass the new status and, on
 * success, the payload - keeping status and data in one mutation makes a
 * COMPLETED document without `extractedData` impossible.
 */
export const setExtractionResult = mutation({
  args: {
    id: v.id('documents'),
    extractionStatus,
    extractedData: v.optional(v.any()),
  },
  handler: async (ctx, args) => {
    const document = await ctx.db.get(args.id);
    if (document === null) throw new Error(`Document ${args.id} not found`);

    await ctx.db.patch(
      args.id,
      touch({
        extractionStatus: args.extractionStatus,
        extractedData: args.extractedData ?? null,
      }),
    );

    await recordAudit(ctx, {
      applicationId: document.applicationId,
      action: 'DOCUMENT_EXTRACTED',
      actor: 'AGENT',
      details: { documentId: args.id, extractionStatus: args.extractionStatus },
    });
  },
});
