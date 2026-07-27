import { defineSchema, defineTable } from 'convex/server';
import { v } from 'convex/values';

/**
 * Convex schema for LoanDoc AI.
 *
 * Conventions applied to every table:
 * - `createdAt` / `updatedAt` are explicit `number` fields (epoch millis).
 *   Convex already stores `_creationTime`, but we keep our own pair because
 *   `updatedAt` has no built-in equivalent and an audit trail must not depend on
 *   a system field whose semantics we do not control.
 * - Loose foreign keys use `v.id('table')`, so a referenced document cannot be
 *   mistyped and the generated TS types flow through to the frontend.
 * - Indexes exist for every access pattern the UI needs; Convex requires an
 *   index for any non-`_id` lookup, so adding a query later without one is a
 *   deliberate decision, not an accident.
 */

/** Enum-like unions are declared once and reused by mutations for validation. */
export const applicationStatus = v.union(
  v.literal('PENDING'),
  v.literal('REVIEW'),
  v.literal('APPROVED'),
  v.literal('DECLINED'),
);

export const extractionStatus = v.union(
  v.literal('PENDING'),
  v.literal('PROCESSING'),
  v.literal('COMPLETED'),
  v.literal('FAILED'),
);

/** Who performed an action. Every mutation records this on the audit log. */
export const actor = v.union(v.literal('AGENT'), v.literal('USER'));

export const userRole = v.union(v.literal('OFFICER'), v.literal('UNDERWRITER'), v.literal('ADMIN'));

export default defineSchema({
  users: defineTable({
    name: v.string(),
    email: v.string(),
    role: userRole,
    createdAt: v.number(),
    updatedAt: v.number(),
  })
    // Unique-by-convention: enforced in the mutation layer, since Convex has no
    // unique constraints.
    .index('by_email', ['email'])
    .index('by_role', ['role']),

  loanApplications: defineTable({
    applicantName: v.string(),
    loanAmount: v.number(),
    status: applicationStatus,
    /** Deterministic rule hits, e.g. 'DTI_ABOVE_43', kept as opaque codes. */
    riskFlags: v.array(v.string()),
    /** Agent-drafted rationale; null until the pipeline has enough evidence. */
    decisionMemo: v.union(v.string(), v.null()),
    assignedOfficerId: v.union(v.id('users'), v.null()),
    /** 0-1. Aggregate of per-field extraction confidence, used for triage. */
    agentConfidenceScore: v.number(),
    createdAt: v.number(),
    updatedAt: v.number(),
  })
    .index('by_status', ['status'])
    .index('by_officer', ['assignedOfficerId'])
    // Powers the review queue: lowest-confidence applications surface first.
    .index('by_status_and_confidence', ['status', 'agentConfidenceScore']),

  documents: defineTable({
    applicationId: v.id('loanApplications'),
    /** Convex storage URL (or any signed URL) - the file itself is not in the DB. */
    fileUrl: v.string(),
    fileName: v.string(),
    /** MIME type as reported by storage, e.g. 'application/pdf'. */
    fileType: v.string(),
    extractionStatus,
    /**
     * Raw extraction payload. Deliberately `v.any()`: the shape varies per
     * document kind and is validated at the edges by the shared Zod schemas in
     * `@loandoc/types` rather than being frozen into the database schema.
     */
    extractedData: v.union(v.any(), v.null()),
    createdAt: v.number(),
    updatedAt: v.number(),
  })
    .index('by_application', ['applicationId'])
    .index('by_extraction_status', ['extractionStatus'])
    .index('by_application_and_status', ['applicationId', 'extractionStatus']),

  auditLogs: defineTable({
    applicationId: v.id('loanApplications'),
    /** Verb phrase, e.g. 'DOCUMENT_EXTRACTED', 'STATUS_CHANGED'. */
    action: v.string(),
    actor,
    details: v.union(v.any(), v.null()),
    /** Which policy version was in force - required to replay a past decision. */
    policyVersion: v.string(),
    /** Separate from createdAt: the event time, which a backfill may predate. */
    timestamp: v.number(),
    createdAt: v.number(),
    updatedAt: v.number(),
  })
    .index('by_application', ['applicationId'])
    .index('by_application_and_timestamp', ['applicationId', 'timestamp'])
    .index('by_actor', ['actor']),

  policies: defineTable({
    name: v.string(),
    /** Semver string; referenced by `auditLogs.policyVersion`. */
    version: v.string(),
    /** Ordered rule definitions, interpreted by the underwriting engine. */
    rules: v.array(v.any()),
    /** Exactly one active version per `name` is enforced in mutations. */
    isActive: v.boolean(),
    description: v.string(),
    createdAt: v.number(),
    updatedAt: v.number(),
  })
    .index('by_name_and_version', ['name', 'version'])
    .index('by_active', ['isActive']),
});
