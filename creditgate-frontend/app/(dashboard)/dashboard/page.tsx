'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Sidebar, type NavKey } from '@/components/dashboard/sidebar'
import { Header } from '@/components/dashboard/header'
import { Overview } from '@/components/dashboard/overview'
import { ApplicationsTab } from '@/components/dashboard/applications-tab'
import { RulesAdmin } from '@/components/dashboard/rules-admin'
import { ExceptionsQueue } from '@/components/dashboard/exceptions-queue'
import { AuditTrail } from '@/components/dashboard/audit-trail'
import { EmptyState } from '@/components/dashboard/primitives'
import { useRole } from '@/lib/role-context'
import { useSelectedApplication, SelectedApplicationProvider } from '@/lib/selected-application-context'
import { TrackedApplicationsProvider } from '@/lib/tracked-applications-context'
import { FileSearch } from 'lucide-react'

function DashboardInner() {
  const [active, setActive] = useState<NavKey>('overview')
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [exceptionCount, setExceptionCount] = useState<number | null>(null)
  const { role, ready } = useRole()
  const { selectedApplicationId, selectApplication } = useSelectedApplication()
  const router = useRouter()

  // No role selected (e.g. deep-linked to /dashboard, or role was cleared) -- send back to the role-select modal.
  useEffect(() => {
    if (ready && !role) router.replace('/')
  }, [ready, role, router])

  function handleSelectApplication(id: string, navigateTo: NavKey) {
    selectApplication(id)
    setActive(navigateTo)
  }

  function handleNavigate(navigateTo: NavKey) {
    setActive(navigateTo)
  }

  if (!ready || !role) {
    return (
      <div className="app-shell">
        <main className="main-content">
          <div className="page-wrap">
            <div className="footer-note"><span><span className="live-dot" /> Loading workspace\u2026</span></div>
          </div>
        </main>
      </div>
    )
  }

  return (
    <div className="app-shell">
      <Sidebar active={active} setActive={setActive} open={sidebarOpen} setOpen={setSidebarOpen} exceptionCount={exceptionCount} />
      <main className="main-content">
        <Header active={active} setOpen={setSidebarOpen} />
        <div className="page-wrap">
          {active === 'overview' && <Overview onNavigate={setActive} />}
          {active === 'applications' && <ApplicationsTab onSelect={handleSelectApplication} onNavigate={handleNavigate} />}
          {active === 'rules' && <RulesAdmin />}
          {active === 'exceptions' && <ExceptionsQueue onCountChange={setExceptionCount} />}
          {active === 'audit' && (
            selectedApplicationId ? (
              <>
                <div className="page-heading">
                  <div>
                    <h1>Audit trail</h1>
                    <p>A complete, immutable record for the currently selected application.</p>
                  </div>
                </div>
                <AuditTrail applicationId={selectedApplicationId} />
              </>
            ) : (
              <>
                <div className="page-heading">
                  <div>
                    <h1>Audit trail</h1>
                  </div>
                </div>
                <EmptyState
                  title="No application selected"
                  description="Pick an application from the Applications tab first -- the audit trail is scoped per application."
                  icon={<FileSearch />}
                />
              </>
            )
          )}
        </div>
      </main>
    </div>
  )
}

export default function DashboardPage() {
  return (
    <TrackedApplicationsProvider>
      <SelectedApplicationProvider>
        <DashboardInner />
      </SelectedApplicationProvider>
    </TrackedApplicationsProvider>
  )
}
