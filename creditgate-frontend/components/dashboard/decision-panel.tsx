'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { RefreshCw, Scale, Workflow } from 'lucide-react'
import { ApiError, getDecision, pollDecision, rerunApplication } from '@/lib/api'
import type { DecisionResponse } from '@/lib/types'
import { Pill, ConditionBadge, ErrorNotice } from './primitives'

const OUTCOME_LABELS: Record<string, string> = {
  STP_APPROVED: 'Straight-through approval',
  HARD_REJECT: 'Hard reject',
  EXCEPTION_REQUIRED: 'Exception required',
  INSUFFICIENT_DATA: 'Insufficient data',
}

function outcomeToPillStatus(outcome: string | null): string {
  if (!outcome) return 'Review'
  if (outcome === 'STP_APPROVED') return 'Approved'
  if (outcome === 'HARD_REJECT') return 'Exception'
  if (outcome === 'EXCEPTION_REQUIRED') return 'Review'
  return 'Review'
}

export function DecisionPanel({ applicationId }: { applicationId: string }) {
  const [decision, setDecision] = useState<DecisionResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [rerunning, setRerunning] = useState(false)
  const [rerunError, setRerunError] = useState<string | null>(null)
  const [previousDecision, setPreviousDecision] = useState<DecisionResponse | null>(null)
  const pollAbortRef = useRef(false)

  const load = useCallback(async (id: string) => {
    setLoading(true)
    setError(null)
    try {
      const initial = await getDecision(id)
      setDecision(initial)
      if (initial.application_status === 'RECEIVED' || initial.application_status === 'PROCESSING') {
        const settled = await pollDecision(id, {
          onTick: (d) => setDecision(d),
        })
        setDecision(settled)
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not load this application\u2019s decision.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    pollAbortRef.current = false
    load(applicationId)
    return () => {
      pollAbortRef.current = true
    }
  }, [applicationId, load])

  async function handleRerun() {
    setRerunning(true)
    setRerunError(null)
    setPreviousDecision(decision)
    try {
      await rerunApplication(applicationId)
      const settled = await pollDecision(applicationId, { onTick: (d) => setDecision(d) })
      setDecision(settled)
    } catch (err) {
      setRerunError(err instanceof ApiError ? err.detail : 'Rerun failed.')
    } finally {
      setRerunning(false)
    }
  }

  if (loading && !decision) {
    return (
      <div className="footer-note">
        <span>
          <span className="live-dot" /> Loading decision\u2026
        </span>
      </div>
    )
  }

  if (error) {
    return <ErrorNotice message={error} onRetry={() => load(applicationId)} />
  }

  if (!decision) return null

  const isProcessing = decision.application_status === 'RECEIVED' || decision.application_status === 'PROCESSING'
  const outcome = decision.effective_outcome ?? decision.outcome

  return (
    <>
      <section className="hero-grid">
        <div className="hero-col hero-col-mid" style={{ gridColumn: '1 / -1' }}>
          <article className="panel decision-card bento-cream">
            <div className="panel-header">
              <div>
                <h2>Decision result</h2>
                <p>Application {decision.application_id.slice(0, 8)}\u2026 \u00b7 status {decision.application_status}</p>
              </div>
              <Pill status={outcomeToPillStatus(outcome)} label={isProcessing ? 'Processing' : OUTCOME_LABELS[outcome ?? ''] ?? outcome ?? 'Unknown'} />
            </div>

            {isProcessing ? (
              <div className="decision-result">
                <span className="decision-icon"><RefreshCw className="spin" /></span>
                <span>Evaluation in progress\u2026 this updates automatically.</span>
              </div>
            ) : (
              <>
                <div className="decision-result">
                  <span className="decision-icon"><Scale /></span>
                  <span>{OUTCOME_LABELS[outcome ?? ''] ?? outcome ?? 'Unknown outcome'}</span>
                  {decision.exception && <strong>{decision.exception.level}</strong>}
                </div>
                <div className="decision-stats">
                  <div>
                    <span>Risk grade</span>
                    <strong>{decision.risk_grade ?? '\u2014'}</strong>
                  </div>
                  <div>
                    <span>Eligible amount</span>
                    <strong>{decision.eligible_amount ? `\u20b9${decision.eligible_amount.toLocaleString('en-IN')}` : '\u2014'}</strong>
                  </div>
                  <div>
                    <span>Interest rate</span>
                    <strong>{decision.interest_rate ? `${decision.interest_rate}%` : '\u2014'}</strong>
                  </div>
                </div>
              </>
            )}

            {decision.application_status === 'FAILED' && (
              <div style={{ padding: '0 19px 19px' }}>
                <ErrorNotice message="Evaluation failed on the backend. Check the audit trail for the EVALUATION_FAILED entry." />
              </div>
            )}
          </article>
        </div>
      </section>

      {previousDecision && !isProcessing && (
        <section className="detail-grid" style={{ marginTop: 14 }}>
          <article className="panel audit-card bento-cream detail-wide">
            <div className="panel-header">
              <div>
                <h2>Before / after rerun</h2>
                <p>What changed after re-evaluating against the current rules</p>
              </div>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th></th>
                    <th>Outcome</th>
                    <th>Risk grade</th>
                    <th>Eligible amount</th>
                    <th>Interest rate</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td className="muted-cell">Before</td>
                    <td>{OUTCOME_LABELS[previousDecision.effective_outcome ?? previousDecision.outcome ?? ''] ?? '\u2014'}</td>
                    <td>{previousDecision.risk_grade ?? '\u2014'}</td>
                    <td className="amount">{previousDecision.eligible_amount ? `\u20b9${previousDecision.eligible_amount.toLocaleString('en-IN')}` : '\u2014'}</td>
                    <td>{previousDecision.interest_rate ? `${previousDecision.interest_rate}%` : '\u2014'}</td>
                  </tr>
                  <tr>
                    <td className="muted-cell">After</td>
                    <td>{OUTCOME_LABELS[decision.effective_outcome ?? decision.outcome ?? ''] ?? '\u2014'}</td>
                    <td>{decision.risk_grade ?? '\u2014'}</td>
                    <td className="amount">{decision.eligible_amount ? `\u20b9${decision.eligible_amount.toLocaleString('en-IN')}` : '\u2014'}</td>
                    <td>{decision.interest_rate ? `${decision.interest_rate}%` : '\u2014'}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </article>
        </section>
      )}

      <section style={{ marginTop: 14 }}>
        <article className="panel trace-panel bento-cream">
          <div className="panel-header">
            <div>
              <h2>Rule trace</h2>
              <p>
                {decision.triggered_rules?.length
                  ? `${decision.triggered_rules.length} rule${decision.triggered_rules.length === 1 ? '' : 's'} evaluated`
                  : isProcessing
                    ? 'Waiting for evaluation to finish\u2026'
                    : 'No rules evaluated yet'}
              </p>
            </div>
            <button className="text-button" onClick={handleRerun} disabled={rerunning || isProcessing} type="button">
              {rerunning ? <RefreshCw className="spin" /> : <Workflow />} {rerunning ? 'Re-running\u2026' : 'Re-run decision'}
            </button>
          </div>
          {rerunError && (
            <div style={{ padding: '0 19px 14px' }}>
              <ErrorNotice message={rerunError} />
            </div>
          )}
          <div className="trace-rows">
            {(decision.triggered_rules ?? []).map((rule) => (
              <div className="trace-row" key={`${rule.rule_code}-v${rule.version}`}>
                <span>
                  {rule.field} <span style={{ opacity: 0.55 }}>({rule.rule_code})</span>
                </span>
                <b>
                  {rule.operator} {JSON.stringify(rule.threshold)} \u2014 actual: {JSON.stringify(rule.actual_value)}
                </b>
                <ConditionBadge conditionMet={rule.condition_met} />
              </div>
            ))}
          </div>
        </article>
      </section>
    </>
  )
}
