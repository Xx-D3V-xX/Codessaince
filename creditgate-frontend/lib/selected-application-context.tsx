'use client'

/**
 * Tracks which application_id the Decision/Rule Trace/Audit panels currently
 * show. Session-only (not persisted) -- selecting an application is a
 * browsing action, not a durable preference like the role is. Cleared
 * naturally on a full page reload, same as re-opening a dashboard tab fresh.
 */

import { createContext, useContext, useState, type ReactNode } from 'react'

interface SelectedApplicationContextValue {
  selectedApplicationId: string | null
  selectApplication: (id: string) => void
  clearSelection: () => void
}

const SelectedApplicationContext = createContext<SelectedApplicationContextValue | undefined>(undefined)

export function SelectedApplicationProvider({ children }: { children: ReactNode }) {
  const [selectedApplicationId, setSelectedApplicationId] = useState<string | null>(null)

  return (
    <SelectedApplicationContext.Provider
      value={{
        selectedApplicationId,
        selectApplication: setSelectedApplicationId,
        clearSelection: () => setSelectedApplicationId(null),
      }}
    >
      {children}
    </SelectedApplicationContext.Provider>
  )
}

export function useSelectedApplication(): SelectedApplicationContextValue {
  const ctx = useContext(SelectedApplicationContext)
  if (!ctx) throw new Error('useSelectedApplication must be used within a SelectedApplicationProvider')
  return ctx
}
