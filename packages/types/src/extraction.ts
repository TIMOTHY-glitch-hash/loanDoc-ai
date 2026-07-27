import { z } from 'zod';
import { documentKindSchema } from './document.js';

/**
 * A single field the agent pulled out of a document. Every extracted value
 * carries its own confidence and provenance (page + bounding box) so the UI can
 * link a number back to the pixels it came from - the audit trail a bank needs.
 */
export const extractedFieldSchema = z.object({
  name: z.string().min(1),
  value: z.string(),
  /** Normalised 0-1. Anything below the review threshold is flagged, not dropped. */
  confidence: z.number().min(0).max(1),
  page: z.number().int().positive(),
  /** [x0, y0, x1, y1] in normalised page coordinates; null when unlocatable. */
  boundingBox: z.tuple([z.number(), z.number(), z.number(), z.number()]).nullable(),
});
export type ExtractedField = z.infer<typeof extractedFieldSchema>;

/** Deterministic rule outcomes (e.g. DTI ratio, stated vs. extracted income). */
export const validationIssueSchema = z.object({
  code: z.string().min(1),
  message: z.string().min(1),
  severity: z.enum(['info', 'warning', 'error']),
});
export type ValidationIssue = z.infer<typeof validationIssueSchema>;

export const extractionResultSchema = z.object({
  documentId: z.string().uuid(),
  /** What the classifier decided, kept separate from the document's stored kind. */
  detectedKind: documentKindSchema,
  fields: z.array(extractedFieldSchema),
  issues: z.array(validationIssueSchema),
  /** Model identifier + latency are surfaced in the UI for cost/perf storytelling. */
  model: z.string().min(1),
  latencyMs: z.number().int().nonnegative(),
});
export type ExtractionResult = z.infer<typeof extractionResultSchema>;
