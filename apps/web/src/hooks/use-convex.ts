'use client';

import { useMutation, useQuery } from 'convex/react';
import { api } from '../../convex/_generated/api';
import type { Doc, Id } from '../../convex/_generated/dataModel';

/**
 * Typed wrappers around Convex's `useQuery` / `useMutation`.
 *
 * Components import these instead of touching `api.*` directly, which keeps
 * function references in one place: renaming a Convex function is then a
 * single-file change, and every call site gets argument types for free.
 *
 * Convex queries are live subscriptions - a mutation anywhere re-renders every
 * subscribed component, so none of these need manual cache invalidation.
 * `undefined` is the loading state (never confuse it with `null`, which means
 * "loaded, does not exist").
 */

export type ApplicationStatus = Doc<'loanApplications'>['status'];
export type ExtractionStatus = Doc<'documents'>['extractionStatus'];
export type Actor = Doc<'auditLogs'>['actor'];

/** Loading is `undefined`; this narrows it for callers that only need a boolean. */
export function isLoading(result: unknown): result is undefined {
  return result === undefined;
}

// --- Loan applications ------------------------------------------------------

export function useLoanApplications(args: { status?: ApplicationStatus; limit?: number } = {}) {
  return useQuery(api.loanApplications.list, args);
}

export function useLoanApplication(id: Id<'loanApplications'> | undefined) {
  // 'skip' defers the subscription until an id exists, avoiding a wasted round
  // trip while a route param is still resolving.
  return useQuery(api.loanApplications.get, id === undefined ? 'skip' : { id });
}

/** Application + documents + audit trail + officer in a single subscription. */
export function useLoanApplicationDetail(id: Id<'loanApplications'> | undefined) {
  return useQuery(api.loanApplications.getWithRelations, id === undefined ? 'skip' : { id });
}

export function useCreateLoanApplication() {
  return useMutation(api.loanApplications.create);
}

export function useSetApplicationStatus() {
  return useMutation(api.loanApplications.setStatus);
}

export function useApplyAgentAssessment() {
  return useMutation(api.loanApplications.applyAgentAssessment);
}

// --- Documents --------------------------------------------------------------

export function useApplicationDocuments(applicationId: Id<'loanApplications'> | undefined) {
  return useQuery(
    api.documents.listByApplication,
    applicationId === undefined ? 'skip' : { applicationId },
  );
}

export function usePendingDocuments(limit?: number) {
  return useQuery(api.documents.listPending, limit === undefined ? {} : { limit });
}

export function useCreateDocument() {
  return useMutation(api.documents.create);
}

export function useSetExtractionResult() {
  return useMutation(api.documents.setExtractionResult);
}

// --- Audit logs -------------------------------------------------------------

export function useAuditLogs(applicationId: Id<'loanApplications'> | undefined, limit?: number) {
  return useQuery(
    api.auditLogs.listByApplication,
    applicationId === undefined
      ? 'skip'
      : { applicationId, ...(limit === undefined ? {} : { limit }) },
  );
}

export function useAppendAuditLog() {
  return useMutation(api.auditLogs.append);
}

// --- Policies ---------------------------------------------------------------

export function usePolicies() {
  return useQuery(api.policies.listAll, {});
}

export function useActivePolicy() {
  return useQuery(api.policies.getActive, {});
}

export function usePublishPolicy() {
  return useMutation(api.policies.publish);
}

// --- Users ------------------------------------------------------------------

export function useUsers(role?: Doc<'users'>['role']) {
  return useQuery(api.users.list, role === undefined ? {} : { role });
}

export function useUpsertUser() {
  return useMutation(api.users.upsert);
}
