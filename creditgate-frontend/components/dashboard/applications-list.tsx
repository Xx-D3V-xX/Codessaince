'use client'

import { ArrowUpRight } from 'lucide-react'
import { useTrackedApplications } from '@/lib/tracked-applications-context'
import type { NavKey } from './sidebar'

export function ApplicationsList({
  onSelect,
  compact,
}: {
  onSelect: (applicationId: string, navigateTo: NavKey) => void
  compact?: boolean
}) {
  const { applications, ready } = useTrackedApplications()

  return (
    <div className="panel applications-panel bento-cream">
      <div className="panel-header">
        <div>
          <h2>{compact ? 'Recent applications' : 'Applications queue'}</h2>
          <p>
            Submitted from this browser this session (the backend has no list-all endpoint yet \u2014 see{' '}
            <code style={{ fontSize: 9 }}>src/api/routers/applicants.py</code>).
          </p>
        </div>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Application</th>
              <th>Applicant</th>
              <th>Submitted</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {applications.slice(0, compact ? 5 : undefined).map((app) => (
              <tr key={app.application_id} onClick={() => onSelect(app.application_id, 'overview')}>
                <td>
                  <div className="applicant-cell">
                    <div className="applicant-avatar">{app.application_id.slice(0, 2)}</div>
                    <div>
                      <strong>{app.application_id.slice(0, 8)}\u2026</strong>
                      <span>full id on click</span>
                    </div>
                  </div>
                </td>
                <td>{app.applicant_id}</td>
                <td className="muted-cell">{new Date(app.submitted_at).toLocaleString()}</td>
                <td>
                  <button className="text-button" onClick={(e) => { e.stopPropagation(); onSelect(app.application_id, 'overview') }}>
                    View <ArrowUpRight />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {ready && applications.length === 0 && (
          <div style={{ padding: '24px 19px', fontSize: 11, color: '#666361' }}>
            No applications submitted yet from this browser. Head to Applications \u2192 New to submit one.
          </div>
        )}
      </div>
    </div>
  )
}
