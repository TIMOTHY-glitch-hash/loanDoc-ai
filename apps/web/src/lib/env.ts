import { z } from 'zod';

/**
 * Fail-fast environment parsing.
 *
 * Next.js inlines `NEXT_PUBLIC_*` at build time, so we must reference each
 * variable literally (a dynamic `process.env[key]` lookup would be replaced with
 * `undefined` in the client bundle).
 */
const clientEnvSchema = z.object({
  // Defaulted so `pnpm build` works in CI without a .env.local; a wrong URL
  // still surfaces immediately as a failed health probe in the UI.
  NEXT_PUBLIC_API_BASE_URL: z.string().url().default('http://localhost:8000'),
  NEXT_PUBLIC_APP_NAME: z.string().min(1).default('LoanDoc AI'),
  // Convex deployment URL, written to .env.local by `npx convex dev`.
  NEXT_PUBLIC_CONVEX_URL: z.string().url().default('https://placeholder.convex.cloud'),
});

const parsed = clientEnvSchema.safeParse({
  NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL,
  NEXT_PUBLIC_APP_NAME: process.env.NEXT_PUBLIC_APP_NAME,
  NEXT_PUBLIC_CONVEX_URL: process.env.NEXT_PUBLIC_CONVEX_URL,
});

if (!parsed.success) {
  // Throwing here surfaces misconfiguration at boot instead of as a confusing
  // `fetch failed` deep inside a server component.
  throw new Error(
    `Invalid client environment:\n${JSON.stringify(parsed.error.flatten().fieldErrors, null, 2)}`,
  );
}

export const env = parsed.data;
