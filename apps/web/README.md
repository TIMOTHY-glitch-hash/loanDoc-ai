# @loandoc/web

Next.js App Router frontend for LoanDoc AI, with Convex as the real-time backend. See the
[root README](../../README.md) for overall architecture and design decisions.

```bash
cp .env.example .env.local
pnpm --filter @loandoc/types build   # the app imports the package's dist/ output
pnpm --filter web convex:dev         # provisions a dev deployment, writes NEXT_PUBLIC_CONVEX_URL, watches convex/
pnpm --filter web convex:seed        # 5 synthetic applications (deterministic faker seed)
pnpm --filter web dev                # http://localhost:3000
```

`convex:dev` must run at least once before `dev`: it creates the deployment and regenerates
`convex/_generated/`. The generated files are committed (Convex's recommendation) so the repo
typechecks on a fresh clone without a deployment.

| Path | Purpose |
| --- | --- |
| `convex/schema.ts` | Table definitions, shared enum validators, indexes |
| `convex/*.ts` | Queries and mutations, one module per table |
| `convex/lib/audit.ts` | `recordAudit` — stamps every entry with the active policy version |
| `convex/lib/timestamps.ts` | `withTimestamps` / `touch` — the only writers of `createdAt`/`updatedAt` |
| `convex/seed.ts` | `internalMutation` seed, run via `pnpm convex:seed` |
| `src/hooks/use-convex.ts` | Typed `useQuery`/`useMutation` wrappers used by components |
| `src/components/convex-provider.tsx` | Client-side `ConvexProvider` mounted in the root layout |
| `src/lib/env.ts` | Fail-fast `NEXT_PUBLIC_*` parsing |
| `src/lib/api.ts` | Typed fetch client for the FastAPI service; responses are Zod-parsed |

## Data model

| Table | Notes |
| --- | --- |
| `users` | `name`, `email`, `role` (OFFICER/UNDERWRITER/ADMIN). Unique-by-email enforced in `users.upsert`. |
| `loanApplications` | `applicantName`, `loanAmount`, `status` (PENDING/REVIEW/APPROVED/DECLINED), `riskFlags`, `decisionMemo`, `assignedOfficerId`, `agentConfidenceScore`. |
| `documents` | `applicationId`, `fileUrl`, `fileName`, `fileType`, `extractionStatus`, `extractedData` (JSON). |
| `auditLogs` | `applicationId`, `action`, `actor` (AGENT/USER), `details` (JSON), `policyVersion`, `timestamp`. Append-only — no update or delete mutation exists. |
| `policies` | `name`, `version`, `rules` (JSON array), `isActive`, `description`. One active version at a time, swapped atomically in `policies.publish`. |

Every table carries `createdAt` / `updatedAt` (epoch millis) alongside Convex's own
`_creationTime`, because `updatedAt` has no built-in equivalent.

## Hooks

Components never reference `api.*` directly; they use the wrappers, so renaming a Convex function
is a one-file change:

```tsx
const applications = useLoanApplications({ status: 'REVIEW' });   // live subscription
const setStatus = useSetApplicationStatus();
if (isLoading(applications)) return <Skeleton />;                  // undefined = loading
await setStatus({ id, status: 'APPROVED', actor: 'USER' });
```

Queries take `'skip'` while an id is still resolving, so no wasted subscription is opened.
