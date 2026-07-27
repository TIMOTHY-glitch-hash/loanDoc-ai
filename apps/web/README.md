# @loandoc/web

Next.js App Router frontend for LoanDoc AI. See the [root README](../../README.md) for architecture,
design decisions and setup.

```bash
cp .env.example .env.local
pnpm --filter @loandoc/types build   # the app imports the package's dist/ output
pnpm --filter web dev                # http://localhost:3000
```

| Path | Purpose |
| --- | --- |
| `src/app` | Routes, layout and metadata |
| `src/components/ui` | shadcn/ui primitives (owned source, not a dependency) |
| `src/lib/env.ts` | Fail-fast `NEXT_PUBLIC_*` parsing |
| `src/lib/api.ts` | Typed fetch client; every response is Zod-parsed |
