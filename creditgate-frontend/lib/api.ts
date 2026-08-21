/**
 * API client for Xx-D3V-xX/Codessaince's FastAPI backend.
 *
 * Base URL resolution mirrors the existing frontend/app.js reference
 * implementation: defaults to http://localhost:8000, overridable via
 * ?api=http://host:port in the URL (useful if the backend isn't on the
 * default port/host). CORS is already open on the backend
 * (allow_origins=["*"]) so no proxy/rewrite is needed.
 */

import type {
  ApplicantListResponse,
  AuditTrailResponse,
  DecisionResponse,
  ExceptionQueueEntry,
  ExceptionResolutionRequest,
  ExceptionResolutionResponse,
  ExceptionLevel,
  ExceptionStatus,
  RerunResponse,
  RuleResponse,
  RuleUpdateRequest,
  SubmitApplicationRequest,
  SubmitApplicationResponse,
  ApplicantPipeline,
  ApiErrorBody,
  DemoFetchRequest,
  DemoFetchResponse,
  DemoStatusResponse,
  GrantConsentRequest,
  GrantConsentResponse,
  OnboardingSection,
  VerifyIdentityRequest,
  VerifyIdentityResponse,
} from './types'

function resolveApiBase(): string {
  if (typeof window !== 'undefined') {
    const override = new URLSearchParams(window.location.search).get('api')
    if (override) return override
  }
  return process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'
}

export class ApiError extends Error {
  status: number
  detail: string
  constructor(status: number, detail: string) {
    super(detail)
    this.status = status
    this.detail = detail
    this.name = 'ApiError'
  }
}

interface RequestOptions extends Omit<RequestInit, 'headers'> {
  headers?: Record<string, string>
}

/**
 * Shared fetch wrapper. Spreads `options` FIRST and sets `headers` LAST --
 * the backend's own frontend/app.js docstring flags the opposite order
 * ({ headers, ...options }) as a real bug: object spread doesn't deep-merge,
 * so options.headers would silently REPLACE the default Content-Type,
 * breaking any call (like exception approve/reject) that passes its own
 * X-User-Role header.
 */
async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const base = resolveApiBase()
  const res = await fetch(`${base}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
  })

  if (!res.ok) {
    let detail = res.statusText
    try {
      const body: ApiErrorBody = await res.json()
      if (body?.detail) detail = body.detail
    } catch {
      // response body wasn't JSON -- fall back to statusText
    }
    throw new ApiError(res.status, detail)
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

// ---------------------------------------------------------------------------
// 1. Application submission
// ---------------------------------------------------------------------------

export function submitApplication(body: SubmitApplicationRequest): Promise<SubmitApplicationResponse> {
  return apiFetch('/applications', { method: 'POST', body: JSON.stringify(body) })
}

// ---------------------------------------------------------------------------
// 2. Decision view (poll + rule trace) + rerun
// ---------------------------------------------------------------------------

export function getDecision(applicationId: string): Promise<DecisionResponse> {
  return apiFetch(`/applications/${applicationId}/decision`)
}

export function rerunApplication(applicationId: string): Promise<RerunResponse> {
  // no request body -- the backend re-fires purely from the stored
  // Application.rule_context_snapshot, see src/api/routers/applications.py
  return apiFetch(`/applications/${applicationId}/rerun`, { method: 'POST' })
}

/**
 * Polls GET /applications/{id}/decision until application_status moves past
 * PROCESSING (i.e. becomes DECISIONED or FAILED), or until timeoutMs elapses.
 * Used after both POST /applications and POST .../rerun, since both are
 * fire-and-return-202 async sagas.
 */
export async function pollDecision(
  applicationId: string,
  opts: { intervalMs?: number; timeoutMs?: number; onTick?: (d: DecisionResponse) => void } = {},
): Promise<DecisionResponse> {
  const intervalMs = opts.intervalMs ?? 700
  const timeoutMs = opts.timeoutMs ?? 30000
  const startedAt = Date.now()

  // eslint-disable-next-line no-constant-condition
  while (true) {
    const decision = await getDecision(applicationId)
    opts.onTick?.(decision)
    if (decision.application_status !== 'RECEIVED' && decision.application_status !== 'PROCESSING') {
      return decision
    }
    if (Date.now() - startedAt > timeoutMs) {
      throw new ApiError(408, `polling timed out after ${timeoutMs}ms (application still ${decision.application_status})`)
    }
    await new Promise((r) => setTimeout(r, intervalMs))
  }
}

// ---------------------------------------------------------------------------
// 3. Rules admin (live threshold editor)
// ---------------------------------------------------------------------------

export function listRules(pipeline: ApplicantPipeline): Promise<RuleResponse[]> {
  return apiFetch(`/rules?pipeline=${encodeURIComponent(pipeline)}`)
}

export function patchRule(ruleCode: string, body: RuleUpdateRequest): Promise<RuleResponse> {
  // keyed by rule_code, NOT id -- id changes on every edit (new version row inserted)
  return apiFetch(`/rules/${encodeURIComponent(ruleCode)}`, { method: 'PATCH', body: JSON.stringify(body) })
}

// ---------------------------------------------------------------------------
// 4. Exception queue (role-gated)
// ---------------------------------------------------------------------------

export function listExceptions(level: ExceptionLevel, status: ExceptionStatus = 'PENDING'): Promise<ExceptionQueueEntry[]> {
  return apiFetch(`/exceptions?level=${encodeURIComponent(level)}&status=${encodeURIComponent(status)}`)
}

export function approveException(
  exceptionId: string,
  body: ExceptionResolutionRequest,
  userRole: string,
): Promise<ExceptionResolutionResponse> {
  return apiFetch(`/exceptions/${exceptionId}/approve`, {
    method: 'POST',
    body: JSON.stringify(body),
    headers: { 'X-User-Role': userRole },
  })
}

export function rejectException(
  exceptionId: string,
  body: ExceptionResolutionRequest,
  userRole: string,
): Promise<ExceptionResolutionResponse> {
  return apiFetch(`/exceptions/${exceptionId}/reject`, {
    method: 'POST',
    body: JSON.stringify(body),
    headers: { 'X-User-Role': userRole },
  })
}

// ---------------------------------------------------------------------------
// 5. Audit trail
// ---------------------------------------------------------------------------

export function getAuditTrail(applicationId: string): Promise<AuditTrailResponse> {
  return apiFetch(`/applications/${applicationId}/audit`)
}

// ---------------------------------------------------------------------------
// Applicants (secondary priority; used by Intake's "existing applicant" picker)
// ---------------------------------------------------------------------------

export function listApplicants(params: { page?: number; pageSize?: number; search?: string; applicantType?: string } = {}): Promise<ApplicantListResponse> {
  const qs = new URLSearchParams()
  if (params.page) qs.set('page', String(params.page))
  if (params.pageSize) qs.set('page_size', String(params.pageSize))
  if (params.search) qs.set('search', params.search)
  if (params.applicantType) qs.set('applicant_type', params.applicantType)
  const suffix = qs.toString() ? `?${qs.toString()}` : ''
  return apiFetch(`/applicants${suffix}`)
}

export { resolveApiBase }

// ---------------------------------------------------------------------------
// Onboarding (new-account flow)
// ---------------------------------------------------------------------------

export function verifyIdentity(body: VerifyIdentityRequest): Promise<VerifyIdentityResponse> {
  return apiFetch('/onboarding/verify-identity', { method: 'POST', body: JSON.stringify(body) })
}

export function grantConsent(body: GrantConsentRequest): Promise<GrantConsentResponse> {
  return apiFetch('/onboarding/consent', { method: 'POST', body: JSON.stringify(body) })
}

export function fetchOnboardingProfile(section: OnboardingSection, body: DemoFetchRequest): Promise<DemoFetchResponse> {
  return apiFetch(`/onboarding/demo/${section}/fetch`, { method: 'POST', body: JSON.stringify(body) })
}

export function getOnboardingStatus(applicationId: string): Promise<DemoStatusResponse> {
  return apiFetch(`/onboarding/status/${applicationId}`)
}

/** same settle-condition as pollDecision, but against the richer onboarding status payload (SHAP + plain-English explanation). */
export async function pollOnboardingStatus(
  applicationId: string,
  opts: { intervalMs?: number; timeoutMs?: number; onTick?: (s: DemoStatusResponse) => void } = {},
): Promise<DemoStatusResponse> {
  const intervalMs = opts.intervalMs ?? 700
  const timeoutMs = opts.timeoutMs ?? 30000
  const startedAt = Date.now()

  // eslint-disable-next-line no-constant-condition
  while (true) {
    const status = await getOnboardingStatus(applicationId)
    opts.onTick?.(status)
    if (status.application_status !== 'RECEIVED' && status.application_status !== 'PROCESSING') {
      return status
    }
    if (Date.now() - startedAt > timeoutMs) {
      throw new ApiError(408, `polling timed out after ${timeoutMs}ms (application still ${status.application_status})`)
    }
    await new Promise((r) => setTimeout(r, intervalMs))
  }
}
