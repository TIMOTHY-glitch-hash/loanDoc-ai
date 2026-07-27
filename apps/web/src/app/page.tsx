import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { getHealth } from '@/lib/api';
import { env } from '@/lib/env';
import type { HealthResponse } from '@loandoc/types';

/**
 * The pipeline stages, kept in the UI layer only - they are presentation copy,
 * not part of the API contract, so they deliberately do not live in
 * `@loandoc/types`.
 */
const PIPELINE_STAGES = [
  { name: 'Ingest', detail: 'PDF/image upload, virus scan, object storage' },
  { name: 'Classify', detail: 'Agent labels each file (pay stub, tax return, ...)' },
  { name: 'Extract', detail: 'Field-level extraction with page + bounding-box provenance' },
  { name: 'Validate', detail: 'Deterministic rules: DTI, income cross-checks, expiry dates' },
  { name: 'Review', detail: 'Low-confidence fields routed to a human underwriter' },
] as const;

/**
 * Server component: the health probe runs on the server, so the browser never
 * needs to reach the API directly on first paint. A failed probe degrades to an
 * "offline" badge instead of breaking the page - the API is optional for a
 * static portfolio deploy.
 */
export default async function HomePage() {
  let health: HealthResponse | null = null;
  try {
    health = await getHealth();
  } catch {
    health = null;
  }

  return (
    <main className="mx-auto w-full max-w-3xl px-6 py-16">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">{env.NEXT_PUBLIC_APP_NAME}</h1>
          <p className="text-muted-foreground mt-2 text-sm">
            Agentic document processing for banking loan underwriting.
          </p>
        </div>
        <Badge variant={health ? 'default' : 'secondary'}>
          {health ? `API v${health.version}` : 'API offline'}
        </Badge>
      </header>

      <Separator className="my-8" />

      <Card>
        <CardHeader>
          <CardTitle>Processing pipeline</CardTitle>
          <CardDescription>
            Each stage is independently observable so a rejected application can always be
            explained.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {PIPELINE_STAGES.map((stage, index) => (
            <div key={stage.name} className="flex gap-4">
              <span className="text-muted-foreground w-6 shrink-0 font-mono text-sm">
                {index + 1}
              </span>
              <div>
                <p className="text-sm font-medium">{stage.name}</p>
                <p className="text-muted-foreground text-sm">{stage.detail}</p>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      <p className="text-muted-foreground mt-8 text-sm">
        {health?.agentEnabled
          ? 'Agent enabled: an LLM provider key is configured on the backend.'
          : 'Agent disabled: set OPENAI_API_KEY on the backend to enable live extraction.'}
      </p>
    </main>
  );
}
