import { faker } from '@faker-js/faker';
import { internalMutation } from './_generated/server';
import type { Doc, Id } from './_generated/dataModel';
import type { MutationCtx } from './_generated/server';
import { withTimestamps } from './lib/timestamps';

/**
 * Development seed: `npx convex run seed:run` (see apps/web/README.md).
 *
 * Declared as an `internalMutation` so it is not reachable from the browser -
 * seeding is an operator action, not part of the app's public API.
 *
 * The faker seed is fixed, so every run produces the same five applications.
 * That makes screenshots, demos and any future snapshot tests reproducible.
 */
const FAKER_SEED = 20250727;

const RISK_FLAG_POOL = [
  'DTI_ABOVE_43',
  'INCOME_MISMATCH',
  'THIN_CREDIT_FILE',
  'RECENT_LARGE_DEPOSIT',
  'ID_EXPIRING_SOON',
  'EMPLOYMENT_UNVERIFIED',
] as const;

const DOCUMENT_TEMPLATES = [
  { fileName: 'paystub-march.pdf', fileType: 'application/pdf' },
  { fileName: 'bank-statement-q1.pdf', fileType: 'application/pdf' },
  { fileName: 'form-1040.pdf', fileType: 'application/pdf' },
  { fileName: 'drivers-license.jpg', fileType: 'image/jpeg' },
] as const;

/** The five applications deliberately span every status the UI must render. */
const STATUSES: readonly Doc<'loanApplications'>['status'][] = [
  'PENDING',
  'REVIEW',
  'REVIEW',
  'APPROVED',
  'DECLINED',
];

const DEFAULT_POLICY_RULES = [
  { code: 'DTI_ABOVE_43', field: 'debtToIncome', operator: 'gt', value: 0.43, severity: 'error' },
  { code: 'MIN_CREDIT_SCORE', field: 'creditScore', operator: 'lt', value: 620, severity: 'error' },
  {
    code: 'INCOME_MISMATCH',
    field: 'statedVsExtractedIncomeDelta',
    operator: 'gt',
    value: 0.1,
    severity: 'warning',
  },
];

async function clearAll(ctx: MutationCtx): Promise<void> {
  // Order matters only for readability; Convex has no FK cascades.
  for (const table of [
    'auditLogs',
    'documents',
    'loanApplications',
    'policies',
    'users',
  ] as const) {
    const rows = await ctx.db.query(table).collect();
    for (const row of rows) {
      await ctx.db.delete(row._id);
    }
  }
}

export const run = internalMutation({
  args: {},
  handler: async (ctx) => {
    faker.seed(FAKER_SEED);
    // Idempotent: re-running the seed replaces the dataset rather than stacking
    // a second copy of it on top.
    await clearAll(ctx);

    const policyVersion = '1.0.0';
    await ctx.db.insert(
      'policies',
      withTimestamps({
        name: 'consumer-mortgage-baseline',
        version: policyVersion,
        rules: DEFAULT_POLICY_RULES,
        isActive: true,
        description:
          'Baseline underwriting rules: DTI ceiling, credit floor, income corroboration.',
      }),
    );

    const officerIds: Id<'users'>[] = [];
    for (const role of ['OFFICER', 'OFFICER', 'UNDERWRITER', 'ADMIN'] as const) {
      const name = faker.person.fullName();
      officerIds.push(
        await ctx.db.insert(
          'users',
          withTimestamps({
            name,
            email: faker.internet.email({ firstName: name.split(' ')[0] }).toLowerCase(),
            role,
          }),
        ),
      );
    }

    const applicationIds: Id<'loanApplications'>[] = [];

    for (const status of STATUSES) {
      const riskFlags = faker.helpers.arrayElements(RISK_FLAG_POOL, { min: 0, max: 3 });
      // APPROVED files are the confident ones; DECLINED/REVIEW sit lower, which
      // is what makes the review queue ordering visible in the UI.
      const agentConfidenceScore =
        status === 'APPROVED'
          ? faker.number.float({ min: 0.9, max: 0.99, fractionDigits: 2 })
          : faker.number.float({ min: 0.42, max: 0.88, fractionDigits: 2 });

      const applicantName = faker.person.fullName();
      const applicationId = await ctx.db.insert(
        'loanApplications',
        withTimestamps({
          applicantName,
          loanAmount: faker.number.int({ min: 85_000, max: 750_000 }),
          status,
          riskFlags,
          decisionMemo:
            status === 'PENDING'
              ? null
              : `${applicantName}: ${riskFlags.length} risk flag(s) reviewed against policy ${policyVersion}. ${faker.lorem.sentence()}`,
          assignedOfficerId: faker.helpers.arrayElement(officerIds),
          agentConfidenceScore,
        }),
      );
      applicationIds.push(applicationId);

      for (const template of faker.helpers.arrayElements(DOCUMENT_TEMPLATES, { min: 2, max: 4 })) {
        const extracted =
          status === 'PENDING'
            ? null
            : {
                grossPay: faker.number.int({ min: 3_000, max: 12_000 }),
                confidence: agentConfidenceScore,
              };
        await ctx.db.insert(
          'documents',
          withTimestamps({
            applicationId,
            // Placeholder URL: no real file is uploaded by the seed.
            fileUrl: `https://example.invalid/seed/${template.fileName}`,
            fileName: template.fileName,
            fileType: template.fileType,
            extractionStatus: status === 'PENDING' ? ('PENDING' as const) : ('COMPLETED' as const),
            extractedData: extracted,
          }),
        );
      }

      // A minimal but plausible history so the timeline view is never empty.
      const events: { action: string; actor: 'AGENT' | 'USER' }[] = [
        { action: 'APPLICATION_CREATED', actor: 'USER' },
        { action: 'DOCUMENT_UPLOADED', actor: 'USER' },
        { action: 'AGENT_ASSESSMENT_RECORDED', actor: 'AGENT' },
        ...(status === 'PENDING' ? [] : [{ action: 'STATUS_CHANGED', actor: 'USER' as const }]),
      ];

      for (const [index, event] of events.entries()) {
        await ctx.db.insert(
          'auditLogs',
          withTimestamps({
            applicationId,
            action: event.action,
            actor: event.actor,
            details: { note: faker.lorem.sentence() },
            policyVersion,
            // Spread events an hour apart so ordering is meaningful.
            timestamp: Date.now() - (events.length - index) * 60 * 60 * 1000,
          }),
        );
      }
    }

    return { applications: applicationIds.length, users: officerIds.length };
  },
});
