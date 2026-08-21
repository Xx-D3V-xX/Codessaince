'use client'

import { useState } from 'react'
import { Check, IdCard, RefreshCw, ShieldCheck, Sparkles, UserPlus } from 'lucide-react'
import {
  ApiError,
  fetchOnboardingProfile,
  grantConsent,
  pollOnboardingStatus,
  verifyIdentity,
} from '@/lib/api'
import type {
  ApplicantType,
  DemoFetchResponse,
  DemoStatusResponse,
  OnboardingSection,
} from '@/lib/types'
import { useSelectedApplication } from '@/lib/selected-application-context'
import { useTrackedApplications } from '@/lib/tracked-applications-context'
import { ErrorNotice, Pill } from './primitives'
import type { NavKey } from './sidebar'

const SECTION_OPTIONS: { value: OnboardingSection; label: string; description: string }[] = [
  { value: 'accept', label: 'Clean file', description: 'Guaranteed STP_APPROVED (bureau score 750\u2013820, no delinquency)' },
  { value: 'reject', label: 'Delinquent', description: 'Guaranteed HARD_REJECT (bureau score 300\u2013579 or a hard negative)' },
  { value: 'l1', label: 'Borderline', description: 'Guaranteed EXCEPTION_REQUIRED / L1 (bureau score 600\u2013699)' },
  { value: 'l2', label: 'High loan amount', description: 'Guaranteed EXCEPTION_REQUIRED / L2, via a forced high requested amount' },
]

const APPLICANT_TYPES: ApplicantType[] = ['SALARIED', 'SELF_EMPLOYED', 'MSME', 'CORPORATE']

type Step = 'identity' | 'consent' | 'factors' | 'result'

/**
 * This is a fixed 4-scenario synthetic-data generator, not free-form
 * applicant creation -- `section` guarantees the outcome; the declared
 * factors below get folded into a synthesized profile shaped to still
 * land there. See src/api/routers/onboarding.py's module docstring.
 */
export function NewAccountOnboarding({ onDone }: { onDone: (navigateTo: NavKey) => void }) {
  const [step, setStep] = useState<Step>('identity')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // step 1: identity
  const [fullName, setFullName] = useState('')
  const [aadhaar, setAadhaar] = useState('')
  const [pan, setPan] = useState('')
  const [kycReferenceId, setKycReferenceId] = useState<string | null>(null)

  // step 2: consent
  const [consentId, setConsentId] = useState<string | null>(null)

  // step 3: scenario + factors
  const [section, setSection] = useState<OnboardingSection>('accept')
  const [applicantType, setApplicantType] = useState<ApplicantType>('SALARIED')
  const [income, setIncome] = useState('60000')
  const [loanAmount, setLoanAmount] = useState('500000')
  const [tenure, setTenure] = useState('24')
  const [age, setAge] = useState('30')
  const [employmentVintage, setEmploymentVintage] = useState('')
  const [businessVintage, setBusinessVintage] = useState('')
  const [obligations, setObligations] = useState('0')

  // step 4: result
  const [fetchResult, setFetchResult] = useState<DemoFetchResponse | null>(null)
  const [status, setStatus] = useState<DemoStatusResponse | null>(null)
  const [polling, setPolling] = useState(false)

  const { selectApplication } = useSelectedApplication()
  const { track } = useTrackedApplications()

  async function handleVerifyIdentity() {
    setBusy(true)
    setError(null)
    try {
      const res = await verifyIdentity({ full_name: fullName, aadhaar_number: aadhaar, pan_number: pan })
      setKycReferenceId(res.kyc_reference_id)
      setStep('consent')
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Identity verification failed.')
    } finally {
      setBusy(false)
    }
  }

  async function handleGrantConsent() {
    if (!kycReferenceId) return
    setBusy(true)
    setError(null)
    try {
      const res = await grantConsent({ kyc_reference_id: kycReferenceId })
      setConsentId(res.consent_id)
      setStep('factors')
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Consent could not be recorded.')
    } finally {
      setBusy(false)
    }
  }

  async function handleFetchAndEvaluate() {
    if (!consentId) return
    setBusy(true)
    setError(null)
    try {
      const res = await fetchOnboardingProfile(section, {
        consent_id: consentId,
        full_name: fullName,
        applicant_type: applicantType,
        declared_income_monthly: Number(income),
        requested_loan_amount: Number(loanAmount),
        requested_tenure_months: Number(tenure),
        age: age ? Number(age) : undefined,
        employment_vintage_months: employmentVintage ? Number(employmentVintage) : undefined,
        business_vintage_months: businessVintage ? Number(businessVintage) : undefined,
        declared_existing_obligations: obligations ? Number(obligations) : 0,
      })
      setFetchResult(res)
      setStep('result')
      track({ application_id: res.application_id, applicant_id: res.applicant_id, submitted_at: new Date().toISOString() })

      setPolling(true)
      const settled = await pollOnboardingStatus(res.application_id, { onTick: setStatus })
      setStatus(settled)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Fetch / evaluation failed.')
    } finally {
      setBusy(false)
      setPolling(false)
    }
  }

  function goToOverview() {
    if (fetchResult) selectApplication(fetchResult.application_id)
    onDone('overview')
  }

  const steps: { key: Step; label: string; icon: typeof IdCard }[] = [
    { key: 'identity', label: 'Verify identity', icon: IdCard },
    { key: 'consent', label: 'Grant consent', icon: ShieldCheck },
    { key: 'factors', label: 'Applicant factors', icon: UserPlus },
    { key: 'result', label: 'Decision', icon: Sparkles },
  ]

  return (
    <>
      <div className="page-heading">
        <div>
          <h1>New account</h1>
          <p>Mock identity verification and Account Aggregator consent, then a synthetic profile is generated and evaluated. See README for what&apos;s mocked vs. real.</p>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 18 }}>
        {steps.map((s, i) => {
          const currentIdx = steps.findIndex((x) => x.key === step)
          const done = i < currentIdx
          const current = i === currentIdx
          return (
            <div
              key={s.key}
              style={{
                display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px', borderRadius: 20,
                fontSize: 10, background: current ? '#2cc4e9' : done ? '#a6dce9' : '#e5e2df',
                color: '#171314', fontWeight: current ? 700 : 500,
              }}
            >
              {done ? <Check size={12} /> : <s.icon size={12} />}
              {s.label}
            </div>
          )
        })}
      </div>

      {error && <ErrorNotice message={error} />}

      {step === 'identity' && (
        <section className="panel rule-preview bento-navy" style={{ maxWidth: 480 }}>
          <div className="panel-header">
            <div>
              <h2>Verify identity</h2>
              <p>Format-only mock check \u2014 not a real UIDAI/NSDL lookup.</p>
            </div>
          </div>
          <div style={{ padding: '0 24px 24px', display: 'flex', flexDirection: 'column', gap: 14 }}>
            <Field label="Full name">
              <input value={fullName} onChange={(e) => setFullName(e.target.value)} style={inputStyle} />
            </Field>
            <Field label="Aadhaar number (12 digits)">
              <input value={aadhaar} onChange={(e) => setAadhaar(e.target.value)} placeholder="123456789012" style={inputStyle} />
            </Field>
            <Field label="PAN (e.g. ABCDE1234F)">
              <input value={pan} onChange={(e) => setPan(e.target.value.toUpperCase())} placeholder="ABCDE1234F" style={inputStyle} />
            </Field>
            <button
              className="primary-button"
              onClick={handleVerifyIdentity}
              disabled={busy || !fullName || !aadhaar || !pan}
              type="button"
              style={{ justifyContent: 'center' }}
            >
              {busy ? <RefreshCw className="spin" /> : <IdCard />} Verify identity
            </button>
          </div>
        </section>
      )}

      {step === 'consent' && (
        <section className="panel rule-preview bento-navy" style={{ maxWidth: 480 }}>
          <div className="panel-header">
            <div>
              <h2>Grant Account Aggregator consent</h2>
              <p>Mock consent gate \u2014 no real AA/FIU integration.</p>
            </div>
          </div>
          <div style={{ padding: '0 24px 24px', display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div className="logic-card">
              <span>KYC REFERENCE</span>
              <strong>{kycReferenceId}</strong>
              <b>Verified</b>
            </div>
            <p style={{ fontSize: 11, color: '#8b9199', margin: 0 }}>
              Requesting scope: BUREAU, BANK_STATEMENT, ITR, ASSETS
            </p>
            <button className="primary-button" onClick={handleGrantConsent} disabled={busy} type="button" style={{ justifyContent: 'center' }}>
              {busy ? <RefreshCw className="spin" /> : <ShieldCheck />} Grant consent
            </button>
          </div>
        </section>
      )}

      {step === 'factors' && (
        <section className="panel rule-preview bento-navy" style={{ maxWidth: 560 }}>
          <div className="panel-header">
            <div>
              <h2>Applicant factors</h2>
              <p>These seed a synthesized bureau/bank/ITR profile shaped to land on the scenario below.</p>
            </div>
          </div>
          <div style={{ padding: '0 24px 24px', display: 'flex', flexDirection: 'column', gap: 14 }}>
            <Field label="Demo scenario (guarantees the outcome)">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {SECTION_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => setSection(opt.value)}
                    style={{
                      textAlign: 'left', padding: '8px 12px', borderRadius: 8, fontSize: 11,
                      border: `1px solid ${section === opt.value ? '#2cc4e9' : '#2a3947'}`,
                      background: section === opt.value ? 'rgba(44,196,233,0.12)' : '#0d151d', color: '#e8eef4',
                    }}
                  >
                    <strong>{opt.label}</strong>
                    <div style={{ opacity: 0.6, fontSize: 10, marginTop: 2 }}>{opt.description}</div>
                  </button>
                ))}
              </div>
            </Field>

            <Field label="Applicant type">
              <select value={applicantType} onChange={(e) => setApplicantType(e.target.value as ApplicantType)} style={inputStyle}>
                {APPLICANT_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </Field>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <Field label="Declared monthly income (\u20b9)"><input type="number" value={income} onChange={(e) => setIncome(e.target.value)} style={inputStyle} /></Field>
              <Field label="Age"><input type="number" value={age} onChange={(e) => setAge(e.target.value)} style={inputStyle} /></Field>
              <Field label="Requested loan amount (\u20b9)"><input type="number" value={loanAmount} onChange={(e) => setLoanAmount(e.target.value)} style={inputStyle} /></Field>
              <Field label="Requested tenure (months)"><input type="number" value={tenure} onChange={(e) => setTenure(e.target.value)} style={inputStyle} /></Field>
              <Field label="Employment vintage (months, optional)"><input type="number" value={employmentVintage} onChange={(e) => setEmploymentVintage(e.target.value)} style={inputStyle} /></Field>
              <Field label="Business vintage (months, optional)"><input type="number" value={businessVintage} onChange={(e) => setBusinessVintage(e.target.value)} style={inputStyle} /></Field>
            </div>
            <Field label="Declared existing obligations (\u20b9/month)">
              <input type="number" value={obligations} onChange={(e) => setObligations(e.target.value)} style={inputStyle} />
            </Field>

            <button
              className="primary-button"
              onClick={handleFetchAndEvaluate}
              disabled={busy || !income || !loanAmount || !tenure}
              type="button"
              style={{ justifyContent: 'center' }}
            >
              {busy ? <RefreshCw className="spin" /> : <Sparkles />} Fetch profile &amp; evaluate
            </button>
            <p style={{ fontSize: 10, color: '#5f6b76', margin: 0 }}>
              This simulates an Account Aggregator data pull (real generation work happens per-call) and immediately submits the resulting profile for evaluation.
            </p>
          </div>
        </section>
      )}

      {step === 'result' && fetchResult && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <section className="panel decision-card bento-cream">
            <div className="panel-header">
              <div>
                <h2>{fetchResult.full_name}</h2>
                <p>{fetchResult.applicant_id} \u00b7 scenario: {fetchResult.section}</p>
              </div>
              {status ? (
                <Pill status={status.effective_outcome ?? status.outcome ?? 'Processing'} />
              ) : (
                <Pill status="Review" label="Processing" />
              )}
            </div>

            {(polling || !status || status.application_status === 'PROCESSING' || status.application_status === 'RECEIVED') && (
              <div className="decision-result">
                <span className="decision-icon"><RefreshCw className="spin" /></span>
                <span>Evaluating\u2026 rules, model scoring, and pricing running now.</span>
              </div>
            )}

            {status && status.application_status !== 'PROCESSING' && status.application_status !== 'RECEIVED' && (
              <>
                <div className="decision-stats">
                  <div><span>Risk grade</span><strong>{status.risk_grade ?? '\u2014'}</strong></div>
                  <div><span>Eligible amount</span><strong>{status.eligible_amount ? `\u20b9${status.eligible_amount.toLocaleString('en-IN')}` : '\u2014'}</strong></div>
                  <div><span>Interest rate</span><strong>{status.interest_rate ? `${status.interest_rate}%` : '\u2014'}</strong></div>
                </div>

                {status.plain_english_explanation && (
                  <div style={{ padding: '0 19px 19px', fontSize: 12, lineHeight: 1.6, color: '#3a3733' }}>
                    {status.plain_english_explanation}
                  </div>
                )}

                {status.top_reasons.length > 0 && (
                  <div style={{ padding: '0 19px 19px' }}>
                    <strong style={{ fontSize: 11 }}>Top reasons</strong>
                    <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 11, color: '#4a4744', display: 'flex', flexDirection: 'column', gap: 4 }}>
                      {status.top_reasons.map((r, i) => <li key={i}>{r}</li>)}
                    </ul>
                  </div>
                )}

                {status.shap_explanation && (
                  <div style={{ padding: '0 19px 19px' }}>
                    <strong style={{ fontSize: 11 }}>Model contribution (SHAP)</strong>
                    <p style={{ fontSize: 10, color: '#666361', margin: '4px 0 8px' }}>
                      Predicted probability {(status.shap_explanation.predicted_probability * 100).toFixed(1)}%, base value {status.shap_explanation.base_value.toFixed(3)}
                    </p>
                    <div className="trace-rows">
                      {status.shap_explanation.top_contributions.map((c) => (
                        <div className="trace-row" key={c.feature}>
                          <span>{c.feature}</span>
                          <b>value: {c.value ?? '\u2014'}</b>
                          <span style={{ fontSize: 10, color: c.shap_value > 0 ? '#a4453f' : '#278c70' }}>
                            {c.shap_value > 0 ? '+' : ''}{c.shap_value.toFixed(4)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </section>

          <button className="primary-button" onClick={goToOverview} type="button" style={{ justifyContent: 'center', maxWidth: 280 }}>
            View in Overview &amp; Audit trail
          </button>
        </div>
      )}
    </>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ fontSize: 10, color: '#8b8b8d', display: 'flex', flexDirection: 'column', gap: 6 }}>
      {label}
      {children}
    </label>
  )
}

const inputStyle: React.CSSProperties = {
  padding: '9px 12px', borderRadius: 8, border: '1px solid #2a3947', background: '#0d151d', color: '#e8eef4', fontSize: 12,
}
