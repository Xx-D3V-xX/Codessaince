'use client'

import { useEffect, useState } from 'react'
import { RefreshCw, Search, Send } from 'lucide-react'
import { ApiError, listApplicants, submitApplication } from '@/lib/api'
import type { ApplicantSummary } from '@/lib/types'
import { useSelectedApplication } from '@/lib/selected-application-context'
import { useTrackedApplications } from '@/lib/tracked-applications-context'
import type { NavKey } from './sidebar'

export function Intake({ onSubmitted }: { onSubmitted: (navigateTo: NavKey) => void }) {
  const [search, setSearch] = useState('')
  const [applicants, setApplicants] = useState<ApplicantSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [selectedApplicantId, setSelectedApplicantId] = useState<string | null>(null)
  const [requestedAmount, setRequestedAmount] = useState('')
  const [requestedTenure, setRequestedTenure] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const { selectApplication } = useSelectedApplication()
  const { track } = useTrackedApplications()

  async function loadApplicants(query: string) {
    setLoading(true)
    setLoadError(null)
    try {
      const res = await listApplicants({ page: 1, pageSize: 10, search: query || undefined })
      setApplicants(res.applicants)
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.detail : 'Could not reach the backend. Is it running on localhost:8000?')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadApplicants('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const t = setTimeout(() => loadApplicants(search), 350)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search])

  const selectedApplicant = applicants.find((a) => a.applicant_id === selectedApplicantId) ?? null

  async function handleSubmit() {
    if (!selectedApplicantId) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      const res = await submitApplication({
        applicant_id: selectedApplicantId,
        requested_loan_amount: requestedAmount ? Number(requestedAmount) : undefined,
        requested_tenure_months: requestedTenure ? Number(requestedTenure) : undefined,
      })
      selectApplication(res.application_id)
      track({ application_id: res.application_id, applicant_id: selectedApplicantId, submitted_at: new Date().toISOString() })
      onSubmitted('overview')
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.detail : 'Submission failed -- check the backend is running.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      <div className="page-heading">
        <div>
          <h1>New application</h1>
          <p>Pick an applicant from the seeded dataset (or a demo-generated one) and submit for evaluation.</p>
        </div>
      </div>

      <div className="rule-layout">
        <section className="panel rule-list bento-cream">
          <div className="panel-header">
            <div>
              <h2>Applicant pool</h2>
              <p>Search by applicant_id, e.g. &quot;APP0001&quot; or &quot;NEW&quot; for demo-generated ones</p>
            </div>
            <button className="ghost-button" onClick={() => loadApplicants(search)} type="button">
              <RefreshCw /> Refresh
            </button>
          </div>

          <div style={{ padding: '0 19px 14px' }}>
            <div style={{ position: 'relative' }}>
              <Search size={14} style={{ position: 'absolute', left: 12, top: 11, opacity: 0.5 }} />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search applicant_id\u2026"
                style={{
                  width: '100%', padding: '9px 12px 9px 32px', borderRadius: 8, border: '1px solid #b7b4b1',
                  background: '#fff', fontSize: 12, color: '#171314',
                }}
              />
            </div>
          </div>

          {loadError && (
            <div className="exception-banner" style={{ margin: '0 19px 14px' }}>
              <div>
                <strong>Couldn&apos;t load applicants</strong>
                <span>{loadError}</span>
              </div>
            </div>
          )}

          {loading && !loadError && (
            <div style={{ padding: '0 19px 14px', fontSize: 11, color: '#666361' }}>Loading\u2026</div>
          )}

          {!loading && !loadError && applicants.length === 0 && (
            <div style={{ padding: '0 19px 20px', fontSize: 11, color: '#666361' }}>No applicants match that search.</div>
          )}

          {applicants.map((a) => (
            <div
              className="rule-row"
              key={a.applicant_id}
              onClick={() => setSelectedApplicantId(a.applicant_id)}
              style={{ cursor: 'pointer', background: selectedApplicantId === a.applicant_id ? '#a6dce9' : undefined }}
            >
              <div className="rule-icon">{a.applicant_id.slice(0, 2)}</div>
              <div>
                <strong>{a.applicant_id}</strong>
                <span>
                  {a.applicant_type ?? 'Unknown type'} \u00b7 age {a.age ?? '\u2014'} \u00b7 requested{' '}
                  {a.requested_loan_amount ? `\u20b9${a.requested_loan_amount.toLocaleString('en-IN')}` : '\u2014'}
                </span>
              </div>
              {a.is_demo_generated && <span className="status-pill status-review"><i className="status-dot" />Demo</span>}
            </div>
          ))}
        </section>

        <section className="panel rule-preview bento-navy">
          <div className="panel-header">
            <div>
              <h2>Submit application</h2>
              <p>{selectedApplicant ? selectedApplicant.applicant_id : 'Select an applicant to continue'}</p>
            </div>
          </div>

          {selectedApplicant ? (
            <div style={{ padding: '0 24px 24px', display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div className="logic-card">
                <span>APPLICANT</span>
                <strong>{selectedApplicant.applicant_id}</strong>
                <b>{selectedApplicant.applicant_type ?? 'Unknown type'}</b>
              </div>

              <label style={{ fontSize: 10, color: '#8b8b8d', display: 'flex', flexDirection: 'column', gap: 6 }}>
                Requested loan amount (optional \u2014 defaults to the applicant&apos;s own request)
                <input
                  type="number"
                  value={requestedAmount}
                  onChange={(e) => setRequestedAmount(e.target.value)}
                  placeholder={selectedApplicant.requested_loan_amount ? String(selectedApplicant.requested_loan_amount) : 'e.g. 500000'}
                  style={{ padding: '9px 12px', borderRadius: 8, border: '1px solid #2a3947', background: '#0d151d', color: '#e8eef4', fontSize: 12 }}
                />
              </label>

              <label style={{ fontSize: 10, color: '#8b8b8d', display: 'flex', flexDirection: 'column', gap: 6 }}>
                Requested tenure, months (optional)
                <input
                  type="number"
                  value={requestedTenure}
                  onChange={(e) => setRequestedTenure(e.target.value)}
                  placeholder={selectedApplicant.requested_tenure_months ? String(selectedApplicant.requested_tenure_months) : 'e.g. 24'}
                  style={{ padding: '9px 12px', borderRadius: 8, border: '1px solid #2a3947', background: '#0d151d', color: '#e8eef4', fontSize: 12 }}
                />
              </label>

              {submitError && <div style={{ fontSize: 11, color: '#f07878' }}>{submitError}</div>}

              <button className="primary-button" onClick={handleSubmit} disabled={submitting} type="button" style={{ justifyContent: 'center' }}>
                {submitting ? <RefreshCw className="spin" /> : <Send />} {submitting ? 'Submitting\u2026' : 'Submit for evaluation'}
              </button>
              <p style={{ fontSize: 10, color: '#5f6b76', margin: 0 }}>
                This returns immediately (202) \u2014 evaluation runs asynchronously. You&apos;ll be taken to Overview to watch it decision.
              </p>
            </div>
          ) : (
            <div style={{ padding: '40px 24px', textAlign: 'center', color: '#5f6b76', fontSize: 12 }}>
              Pick an applicant from the list on the left.
            </div>
          )}
        </section>
      </div>
    </>
  )
}
