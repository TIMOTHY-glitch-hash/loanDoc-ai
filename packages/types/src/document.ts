import { z } from 'zod';

/**
 * The document taxonomy an underwriter deals with. Modelled as a Zod enum
 * (rather than a bare TS union) so the same list can validate untrusted API
 * payloads at runtime *and* generate the compile-time union below.
 */
export const documentKindSchema = z.enum([
  'pay_stub',
  'bank_statement',
  'tax_return',
  'id_document',
  'property_appraisal',
  'unknown',
]);
export type DocumentKind = z.infer<typeof documentKindSchema>;

/**
 * Lifecycle of a document inside the pipeline. `needs_review` is the important
 * one for underwriting: it means the agent produced output but confidence fell
 * below the configured threshold, so a human must confirm it.
 */
export const documentStatusSchema = z.enum([
  'uploaded',
  'classifying',
  'extracting',
  'needs_review',
  'verified',
  'failed',
]);
export type DocumentStatus = z.infer<typeof documentStatusSchema>;

export const loanDocumentSchema = z.object({
  id: z.string().uuid(),
  /** Owning loan application; documents are never global. */
  applicationId: z.string().uuid(),
  fileName: z.string().min(1),
  /** Bytes, as reported by the storage layer (not the browser). */
  sizeBytes: z.number().int().nonnegative(),
  mimeType: z.string().min(1),
  kind: documentKindSchema,
  status: documentStatusSchema,
  /** ISO-8601 UTC timestamps; serialised as strings across the wire. */
  uploadedAt: z.string().datetime(),
  processedAt: z.string().datetime().nullable(),
});
export type LoanDocument = z.infer<typeof loanDocumentSchema>;
