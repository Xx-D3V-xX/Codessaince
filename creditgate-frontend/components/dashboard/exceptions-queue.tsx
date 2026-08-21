'use client'

import { useEffect, useState } from 'react'
import { AlertTriangle, Check, RefreshCw, X } from 'lucide-react'
import { ApiError, approveException, listExceptions, rejectException } from '@/lib/api'
import type { ExceptionLevel, ExceptionQueueEntry } from '@/lib/types'
import { useRole } from '@/lib/role-context'
import { Pill, ErrorNotice } from './primitives'

/** which levels a role can even see in its own queue -- server still enforces this on approve/reject, this is client convenience per the README. */
const LEVELS_VISIBLE_TO_ROLE: Record<string, ExceptionLevel[]> = {
  credit_ops_l1: ['L1'],
  credit_ops_l2: ['L2'],
  credit_head: ['L1', 'L2', 'CREDIT_HEAD'],
}

export function ExceptionsQueue({ onCountChange }: { onCountChange?: (count: number) => void }) {
  const { role } = useRole()
  const visibleLevels = role ? LEVELS_VISIBLE_TO_ROLE[role] : []
  const [level, setLevel] = useState<ExceptionLevel>(visibleLevels[0] ?? 'L1')
  const [entries, setEntries] = useState<ExceptionQueueEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actioningId, setActioningId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [notes, setNotes] = useState<Record<string, string>>({})

  async function load(lvl: ExceptionLevel) {
    setLoading(true)
    setError(null)
    try {
      const res = await listExceptions(lvl, 'PENDING')
      setEntries(res)
      onCountChange?.(res.length)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not load the exception queue.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (visibleLevels.length && !visibleLevels.includes(level)) {
      setLevel(visibleLevels[0])
      return
    }
    load(level)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [level, role])

  async function handleAction(entry: ExceptionQueueEntry, action: 'approve' | 'reject') {
    if (!role) return
    setActioningId(entry.id)
    setActionError(null)
    try {
      const fn = action === 'approve' ? approveException : rejectException
      await fn(entry.id, { resolved_by: role, notes: notes[entry.id] || undefined }, role)
      setEntries((prev) => prev.filter((e) => e.id !== entry.id))
      onCountChange?.(entries.length - 1)
    } catch (err) {
      setActionError(
        err instanceof ApiError
          ? `${err.status === 403 ? 'Not authorized: ' : ''}${err.detail}`
          : 'Action failed.',
      )
    } finally {
      setActioningId(null)
    }
  }

  if (!role) {
    return <ErrorNotice message="Select a role from the header to view and act on exceptions." />
  }

  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Exceptions</h1>
          <p>Cases paused for human review. Approve/reject sends X-User-Role: {role}.</p>
        </div>
        <div className="heading-actions">
          <div className="range-toggle">
            {visibleLevels.map((lvl) => (
              <button key={lvl} className={level === lvl ? 'selected' : ''} onClick={() => setLevel(lvl)} type="button">
                {lvl}
              </button>
            ))}
          </div>
          <button className="ghost-button" onClick={() => load(level)} type="button">
            <RefreshCw /> Refresh
          </button>
        </div>
      </div>

      {entries.length > 0 && (
        <div className="exception-banner">
          <AlertTriangle />
          <div>
            <strong>{entries.length} exception{entries.length === 1 ? '' : 's'} pending at {level}</strong>
            <span>Review each case&apos;s decision context before approving or rejecting.</span>
          </div>
        </div>
      )}

      {error && <ErrorNotice message={error} onRetry={() => load(level)} />}
      {actionError && <ErrorNotice message={actionError} />}

      {loading && !error ? (
        <div className="footer-note"><span><span className="live-dot" /> Loading queue\u2026</span></div>
      ) : (
        <div className="panel applications-panel bento-cream">
          <div className="panel-header">
            <div>
              <h2>Queue \u2014 {level}</h2>
              <p>{entries.length} pending</p>
            </div>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Applicant</th>
                  <th>Outcome</th>
                  <th>Risk grade</th>
                  <th>Eligible amount</th>
                  <th>Created</th>
                  <th>Notes</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry) => (
                  <tr key={entry.id}>
                    <td>
                      <div className="applicant-cell">
                        <div className="applicant-avatar">{entry.applicant_id.slice(0, 2)}</div>
                        <div>
                          <strong>{entry.applicant_id}</strong>
                          <span>{entry.application_id.slice(0, 8)}\u2026</span>
                        </div>
                      </div>
                    </td>
                    <td><Pill status={entry.decision_outcome} /></td>
                    <td>{entry.risk_grade ?? '\u2014'}</td>
                    <td className="amount">{entry.eligible_amount ? `\u20b9${entry.eligible_amount.toLocaleString('en-IN')}` : '\u2014'}</td>
                    <td className="muted-cell">{new Date(entry.created_at).toLocaleString()}</td>
                    <td>
                      <input
                        value={notes[entry.id] ?? ''}
                        onChange={(e) => setNotes((prev) => ({ ...prev, [entry.id]: e.target.value }))}
                        placeholder="Optional note"
                        style={{ padding: '5px 8px', borderRadius: 6, border: '1px solid #b7b4b1', fontSize: 10, width: 120 }}
                      />
                    </td>
                    <td>
                      <div style={{ display: 'flex', gap: 6 }}>
                        <button
                          className="icon-button"
                          onClick={() => handleAction(entry, 'approve')}
                          disabled={actioningId === entry.id}
                          type="button"
                          aria-label="Approve"
                          style={{ color: '#278c70' }}
                        >
                          {actioningId === entry.id ? <RefreshCw className="spin" /> : <Check />}
                        </button>
                        <button
                          className="icon-button"
                          onClick={() => handleAction(entry, 'reject')}
                          disabled={actioningId === entry.id}
                          type="button"
                          aria-label="Reject"
                          style={{ color: '#a4453f' }}
                        >
                          <X />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!loading && entries.length === 0 && (
              <div style={{ padding: '24px 19px', fontSize: 11, color: '#666361' }}>No pending exceptions at {level}.</div>
            )}
          </div>
        </div>
      )}
    </>
  )
}
