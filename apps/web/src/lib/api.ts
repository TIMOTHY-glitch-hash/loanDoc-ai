import { apiErrorSchema, healthResponseSchema, type HealthResponse } from '@loandoc/types';
import { env } from './env';

/** Thrown for any non-2xx response so callers can branch on status/code. */
export class ApiRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = 'ApiRequestError';
  }
}

/**
 * Thin typed fetch wrapper around the FastAPI backend.
 *
 * Every response is parsed with the shared Zod schema rather than cast, so a
 * backend contract change fails loudly in one place instead of producing
 * `undefined` somewhere in the React tree.
 */
async function request<T>(
  path: string,
  schema: { parse: (data: unknown) => T },
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(new URL(path, env.NEXT_PUBLIC_API_BASE_URL), {
    ...init,
    headers: { Accept: 'application/json', ...init?.headers },
    // Underwriting data is never stale-cacheable; opt out explicitly.
    cache: 'no-store',
  });

  const body: unknown = await response.json().catch(() => null);

  if (!response.ok) {
    const parsedError = apiErrorSchema.safeParse(body);
    throw new ApiRequestError(
      parsedError.success ? parsedError.data.detail : `Request to ${path} failed`,
      response.status,
      parsedError.success ? parsedError.data.code : undefined,
    );
  }

  return schema.parse(body);
}

export function getHealth(): Promise<HealthResponse> {
  return request('/api/v1/health', healthResponseSchema);
}
