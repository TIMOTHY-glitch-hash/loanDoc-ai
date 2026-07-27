/* eslint-disable */
/**
 * Generated `api` utility.
 *
 * THIS CODE IS AUTOMATICALLY GENERATED.
 *
 * To regenerate, run `npx convex dev`.
 * @module
 */

import type { ApiFromModules, FilterApi, FunctionReference } from 'convex/server';
import type * as auditLogs from '../auditLogs.js';
import type * as documents from '../documents.js';
import type * as lib_audit from '../lib/audit.js';
import type * as lib_timestamps from '../lib/timestamps.js';
import type * as loanApplications from '../loanApplications.js';
import type * as policies from '../policies.js';
import type * as seed from '../seed.js';
import type * as users from '../users.js';

/**
 * A utility for referencing Convex functions in your app's API.
 *
 * Usage:
 * ```js
 * const myFunctionReference = api.myModule.myFunction;
 * ```
 */
declare const fullApi: ApiFromModules<{
  auditLogs: typeof auditLogs;
  documents: typeof documents;
  'lib/audit': typeof lib_audit;
  'lib/timestamps': typeof lib_timestamps;
  loanApplications: typeof loanApplications;
  policies: typeof policies;
  seed: typeof seed;
  users: typeof users;
}>;
export declare const api: FilterApi<typeof fullApi, FunctionReference<any, 'public'>>;
export declare const internal: FilterApi<typeof fullApi, FunctionReference<any, 'internal'>>;
