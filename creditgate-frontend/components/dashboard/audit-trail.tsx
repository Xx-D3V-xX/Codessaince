'use client'

import { useEffect, useState } from 'react'
import { FileSearch, RefreshCw } from 'lucide-react'
import { ApiError, getAuditTrail } from '@/lib/api'
import type { AuditLogEntry } from '@/lib/types'
import { ErrorNotice } from './primitives'

export function AuditTrail({ applicationId }: { applicationId: string }) {
  const [entries, setEntries] = useState<AuditLogEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const res = await getAuditTrail(applicationId)
      setEntries(res.entries)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not load the audit trail.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applicationId])

  if (loading) return <div className="footer-note"><span><span className="live-dot" /> Loading audit trail\u2026</span></div>
  if (error) return <ErrorNotice message={error} onRetry={load} />

  return (
    <article className="panel audit-card bento-cream" style={{ width: '100%' }}>
      <div className="panel-header">
        <div>
          <h2>Audit trail</h2>
          <p>Application {applicationId.slice(0, 8)}\u2026 \u00b7 {entries.length} event{entries.length === 1 ? '' : 's'}, chronological</p>
        </div>
        <span className="icon-badge"><FileSearch /></span>
      </div>
      <div className="audit-list" style={{ padding: '0 19px 19px' }}>
        {entries.map((entry) => {
          const expanded = expandedId === entry.id
          return (
            <div key={entry.id} style={{ borderBottom: '1px solid #c5c2bf', padding: '10px 0', cursor: 'pointer' }} onClick={() => setExpandedId(expanded ? null : entry.id)}>
              <div style={{ display: 'flex', gap: 12, alignItems: 'baseline' }}>
                <code style={{ fontSize: 10, color: '#666361', minWidth: 130 }}>{new Date(entry.timestamp).toLocaleString()}</code>
                <span style={{ fontSize: 11 }}>
                  <strong>{entry.actor}</strong> \u00b7 {entry.action} \u00b7 <span style={{ opacity: 0.6 }}>{entry.entity_type}</span>
                </span>
              </div>
              {expanded && (entry.before || entry.after) && (
                <div style={{ marginTop: 8, display: 'flex', gap: 12, fontSize: 10 }}>
                  {entry.before && (
                    <pre style={{ flex: 1, background: '#0d151d', color: '#e8eef4', padding: 10, borderRadius: 6, overflowX: 'auto', margin: 0 }}>
                      {JSON.stringify(entry.before, null, 2)}
                    </pre>
                  )}
                  {entry.after && (
                    <pre style={{ flex: 1, background: '#0d151d', color: '#e8eef4', padding: 10, borderRadius: 6, overflowX: 'auto', margin: 0 }}>
                      {JSON.stringify(entry.after, null, 2)}
                    </pre>
                  )}
                </div>
              )}
            </div>
          )
        })}
        {entries.length === 0 && <div style={{ padding: '20px 0', fontSize: 11, color: '#666361' }}>No audit events yet for this application.</div>}
      </div>
    </article>
  )
}
