/**
 * Types mirroring src/api/schemas.py in Xx-D3V-xX/Codessaince (backend repo).
 * Kept 1:1 with the Pydantic models -- see that file as the source of truth
 * if these ever need to change. UUIDs and datetimes serialize as strings
 * over the wire, so they're typed as `string` here, not branded types.
 */

export type ApplicantPipeline = 'INDIVIDUAL' | 'MSME'

export type ApplicantType = 'SALARIED' | 'SELF_EMPLOYED' | 'MSME' | 'CORPORATE'

export type ApplicationStatus = 'RECEIVED' | 'PROCESSING' | 'DECISIONED' | 'FAILED'

export type RuleOperator =
  | 'LT'
  | 'LTE'
  | 'GT'
  | 'GTE'
  | 'EQ'
  | 'NEQ'
  | 'IN'
  | 'NOT_IN'
  | 'IS_NULL'
  | 'IS_NOT_NULL'

export type RuleOutcome = 'HARD_REJECT' | 'EXCEPTION'

export type ExceptionLevel = 'L1' | 'L2' | 'CREDIT_HEAD'

export type ExceptionStatus = 'PENDING' | 'APPROVED' | 'REJECTED'

export type DecisionOutcome = 'STP_APPROVED' | 'HARD_REJECT' | 'EXCEPTION_REQUIRED' | 'INSUFFICIENT_DATA'

/** the three roles the role-select modal offers -- see src/api/deps.py's _ROLES_ALLOWED_FOR_LEVEL */
export type UserRole = 'credit_ops_l1' | 'credit_ops_l2' | 'credit_head'

// ---------------------------------------------------------------------------
// Applications
// ---------------------------------------------------------------------------

export interface SubmitApplicationRequest {
  applicant_id: string
  requested_loan_amount?: number | null
  requested_tenure_months?: number | null
}

export interface SubmitApplicationResponse {
  application_id: string
  status: string
  message: string
}

export interface RuleTraceEntry {
  rule_code: string
  version: number
  pipeline: string
  field: string
  operator: string
  actual_value: unknown
  threshold: Record<string, unknown>
  /** three-valued: true=fired, false=checked and clear, null=couldn't evaluate. NEVER coerce to false. */
  condition_met: boolean | null
  outcome: string
  severity: string | null
  reason_code: string
  priority: number
  rule_group: string
}

export interface ExceptionSummary {
  id: string
  level: string
  status: string
  assigned_to: string | null
  resolved_by: string | null
  resolved_at: string | null
  notes: string | null
}

export interface DecisionResponse {
  application_id: string
  application_status: ApplicationStatus | string
  decision_id: string | null
  outcome: DecisionOutcome | string | null
  effective_outcome: string | null
  risk_grade: string | null
  eligible_amount: number | null
  interest_rate: number | null
  tenure_months: number | null
  model_risk_score: number | null
  decided_at: string | null
  is_current: boolean | null
  rule_version_snapshot: Record<string, unknown> | null
  triggered_rules: RuleTraceEntry[] | null
  exception: ExceptionSummary | null
}

export interface RerunResponse {
  application_id: string
  status: string
  message: string
}

// ---------------------------------------------------------------------------
// Rules
// ---------------------------------------------------------------------------

export interface RuleUpdateRequest {
  edited_by: string
  field?: string
  operator?: RuleOperator
  /** e.g. {"threshold": 700} -- always a dict, never a raw scalar. See Rule.value in src/db/models.py. */
  value?: Record<string, unknown>
  outcome?: RuleOutcome
  severity?: ExceptionLevel
  reason_code?: string
  priority?: number
  rule_group?: string
}

export interface RuleResponse {
  id: string
  rule_code: string
  version: number
  pipeline: string
  field: string
  operator: string
  value: Record<string, unknown>
  outcome: string
  severity: string | null
  reason_code: string
  priority: number
  rule_group: string
  active: boolean
  effective_from: string
  effective_to: string | null
}

// ---------------------------------------------------------------------------
// Exceptions
// ---------------------------------------------------------------------------

export interface ExceptionQueueEntry {
  id: string
  level: string
  status: string
  assigned_to: string | null
  application_id: string
  applicant_id: string
  decision_outcome: string
  risk_grade: string | null
  eligible_amount: number | null
  created_at: string
}

export interface ExceptionResolutionRequest {
  resolved_by: string
  notes?: string | null
}

export interface ExceptionResolutionResponse {
  exception: ExceptionSummary
  decision: DecisionResponse
}

// ---------------------------------------------------------------------------
// Audit
// ---------------------------------------------------------------------------

export interface AuditLogEntry {
  id: string
  actor: string
  action: string
  entity_type: string
  entity_id: string
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  timestamp: string
}

export interface AuditTrailResponse {
  application_id: string
  entries: AuditLogEntry[]
}

// ---------------------------------------------------------------------------
// Applicants (secondary priority, but needed by Intake's applicant picker)
// ---------------------------------------------------------------------------

export interface ApplicantSummary {
  applicant_id: string
  applicant_type: string | null
  age: number | null
  declared_income_monthly: number | null
  requested_loan_amount: number | null
  requested_tenure_months: number | null
  is_demo_generated: boolean
}

export interface ApplicantListResponse {
  total: number
  page: number
  page_size: number
  total_pages: number
  applicants: ApplicantSummary[]
}

// ---------------------------------------------------------------------------
// Onboarding (new-account flow): verify identity -> grant consent ->
// fetch synthetic profile for one of 4 fixed demo scenarios -> poll status.
// See src/api/routers/onboarding.py.
// ---------------------------------------------------------------------------

export type OnboardingSection = 'accept' | 'reject' | 'l1' | 'l2'

export interface VerifyIdentityRequest {
  full_name: string
  aadhaar_number: string
  pan_number: string
}

export interface VerifyIdentityResponse {
  verified: boolean
  kyc_reference_id: string
  message: string
}

export interface GrantConsentRequest {
  kyc_reference_id: string
  consent_scope?: string[]
}

export interface GrantConsentResponse {
  consent_id: string
  scope: string[]
  message: string
}

export interface DemoFetchRequest {
  consent_id: string
  full_name: string
  applicant_type: ApplicantType
  declared_income_monthly: number
  requested_loan_amount: number
  requested_tenure_months: number
  loan_type?: string | null
  age?: number
  employment_vintage_months?: number | null
  business_vintage_months?: number | null
  declared_existing_obligations?: number
}

export interface GeneratedProfile {
  master_profile: Record<string, unknown>
  bureau_data: Record<string, unknown>
  bank_statement_data: Record<string, unknown>
  itr_data: Record<string, unknown>[]
  assets_data: Record<string, unknown>
  feature_vector: Record<string, unknown>
}

export interface DemoFetchResponse {
  application_id: string
  applicant_id: string
  full_name: string
  section: string
  application_status: string
  generated_profile: GeneratedProfile
  message: string
}

export interface ShapContribution {
  feature: string
  value: number | null
  shap_value: number
}

export interface ShapExplanation {
  base_value: number
  predicted_probability: number
  top_contributions: ShapContribution[]
}

export interface DemoStatusResponse {
  application_id: string
  application_status: string
  outcome: string | null
  effective_outcome: string | null
  risk_grade: string | null
  eligible_amount: number | null
  interest_rate: number | null
  tenure_months: number | null
  model_risk_score: number | null
  triggered_rules: RuleTraceEntry[] | null
  reason_codes: string[]
  top_reasons: string[]
  plain_english_explanation: string | null
  shap_explanation: ShapExplanation | null
}

// ---------------------------------------------------------------------------
// API error shape (FastAPI's default HTTPException body)
// ---------------------------------------------------------------------------

export interface ApiErrorBody {
  detail: string
}
