import { z } from 'zod';
import { loanDocumentSchema } from './document.js';
import { extractionResultSchema } from './extraction.js';

/**
 * Envelope every FastAPI error handler returns. Mirroring it here means the web
 * client can narrow on `detail` without `any` casts.
 */
export const apiErrorSchema = z.object({
  detail: z.string(),
  code: z.string().optional(),
});
export type ApiError = z.infer<typeof apiErrorSchema>;

export const healthResponseSchema = z.object({
  status: z.literal('ok'),
  version: z.string(),
  /** Feature flags let the UI degrade gracefully when no LLM key is configured. */
  agentEnabled: z.boolean(),
});
export type HealthResponse = z.infer<typeof healthResponseSchema>;

/** Cursor-style pagination keeps the contract stable if we move off offsets. */
export const documentListResponseSchema = z.object({
  items: z.array(loanDocumentSchema),
  total: z.number().int().nonnegative(),
  nextCursor: z.string().nullable(),
});
export type DocumentListResponse = z.infer<typeof documentListResponseSchema>;

export const processDocumentResponseSchema = z.object({
  document: loanDocumentSchema,
  extraction: extractionResultSchema,
});
export type ProcessDocumentResponse = z.infer<typeof processDocumentResponseSchema>;
