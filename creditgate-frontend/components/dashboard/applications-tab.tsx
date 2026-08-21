'use client'

import { useState } from 'react'
import { Plus, UserPlus } from 'lucide-react'
import { ApplicationsList } from './applications-list'
import { Intake } from './intake'
import { NewAccountOnboarding } from './new-account-onboarding'
import type { NavKey } from './sidebar'

export function ApplicationsTab({
  onSelect,
  onNavigate,
}: {
  onSelect: (applicationId: string, navigateTo: NavKey) => void
  onNavigate: (navigateTo: NavKey) => void
}) {
  const [mode, setMode] = useState<'list' | 'new' | 'onboard'>('list')

  if (mode === 'new') {
    return <Intake onSubmitted={() => setMode('list')} />
  }

  if (mode === 'onboard') {
    return <NewAccountOnboarding onDone={onNavigate} />
  }

  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Applications</h1>
          <p>Every application submitted through this workspace.</p>
        </div>
        <div className="heading-actions">
          <button className="ghost-button" onClick={() => setMode('onboard')} type="button">
            <UserPlus /> New account
          </button>
          <button className="primary-button" onClick={() => setMode('new')} type="button">
            <Plus /> Existing applicant
          </button>
        </div>
      </div>
      <ApplicationsList onSelect={onSelect} />
    </>
  )
}
