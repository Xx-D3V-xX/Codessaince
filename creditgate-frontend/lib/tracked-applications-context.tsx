'use client'

/**
 * The backend has no GET /applications (list) endpoint -- only
 * GET /applications/{id}/decision and .../audit, both of which require
 * already knowing the id. There is no way to ask the backend "what has
 * been submitted so far."
 *
 * This is a client-side, localStorage-backed stand-in: every successful
 * POST /applications response gets recorded here, becoming the
 * "Applications" table and the thing Overview/Decision/Audit select from.
 * It only reflects what THIS browser has submitted (survives refresh, not
 * shared across devices/browsers) -- a real limitation, not a cosmetic one,
 * disclosed in the Applications panel itself.
 */

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

export interface TrackedApplication {
  application_id: string
  applicant_id: string
  submitted_at: string // ISO timestamp, client-side
}

const STORAGE_KEY = 'creditgate.trackedApplications'
const MAX_TRACKED = 200

interface TrackedApplicationsContextValue {
  applications: TrackedApplication[]
  track: (entry: TrackedApplication) => void
  ready: boolean
}

const TrackedApplicationsContext = createContext<TrackedApplicationsContextValue | undefined>(undefined)

export function TrackedApplicationsProvider({ children }: { children: ReactNode }) {
  const [applications, setApplications] = useState<TrackedApplication[]>([])
  const [ready, setReady] = useState(false)

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY)
      if (stored) setApplications(JSON.parse(stored))
    } catch {
      // corrupt or unavailable storage -- start empty rather than throwing
    }
    setReady(true)
  }, [])

  function track(entry: TrackedApplication) {
    setApplications((prev) => {
      const next = [entry, ...prev.filter((a) => a.application_id !== entry.application_id)].slice(0, MAX_TRACKED)
      try {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
      } catch {
        // ignore -- still works for this session via React state
      }
      return next
    })
  }

  return <TrackedApplicationsContext.Provider value={{ applications, track, ready }}>{children}</TrackedApplicationsContext.Provider>
}

export function useTrackedApplications(): TrackedApplicationsContextValue {
  const ctx = useContext(TrackedApplicationsContext)
  if (!ctx) throw new Error('useTrackedApplications must be used within a TrackedApplicationsProvider')
  return ctx
}
