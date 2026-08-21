'use client'

import { FilePlus } from 'lucide-react'
import { useSelectedApplication } from '@/lib/selected-application-context'
import { DecisionPanel } from './decision-panel'
import { AuditTrail } from './audit-trail'
import { ApplicationsList } from './applications-list'
import { EmptyState } from './primitives'
import type { NavKey } from './sidebar'

export function Overview({ onNavigate }: { onNavigate: (navigateTo: NavKey) => void }) {
  const { selectedApplicationId, selectApplication } = useSelectedApplication()

  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Overview</h1>
          <p>{selectedApplicationId ? `Application ${selectedApplicationId.slice(0, 8)}\u2026` : 'Select or submit an application to see its decision.'}</p>
        </div>
      </div>

      {selectedApplicationId ? (
        <>
          <DecisionPanel applicationId={selectedApplicationId} />
          <section style={{ marginTop: 14 }}>
            <AuditTrail applicationId={selectedApplicationId} />
          </section>
        </>
      ) : (
        <EmptyState
          title="No application selected"
          description="Submit a new application or pick one from the Applications tab to see its decision, rule trace, and audit history here."
          icon={<FilePlus />}
        />
      )}

      <section style={{ marginTop: 14 }}>
        <ApplicationsList onSelect={(id) => { selectApplication(id); onNavigate('overview') }} compact />
      </section>
    </>
  )
}
