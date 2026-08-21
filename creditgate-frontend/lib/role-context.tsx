'use client'

/**
 * Client-side role selection. There is no auth system on the backend --
 * see src/api/deps.py's own docstring: role-gating is genuine, testable
 * authorization LOGIC, but the identity side (proving the role wasn't
 * forged) is explicitly out of scope. The login modal is repurposed as a
 * role SELECT, not a real login (README: "no API call on login").
 *
 * The picked role is kept in localStorage (survives refresh) and sent as
 * the X-User-Role header on every exception approve/reject request.
 */

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import type { UserRole } from './types'

const STORAGE_KEY = 'creditgate.role'

export const ROLE_LABELS: Record<UserRole, string> = {
  credit_ops_l1: 'Credit Ops \u2014 L1',
  credit_ops_l2: 'Credit Ops \u2014 L2',
  credit_head: 'Credit Head',
}

export const ALL_ROLES: UserRole[] = ['credit_ops_l1', 'credit_ops_l2', 'credit_head']

interface RoleContextValue {
  role: UserRole | null
  setRole: (role: UserRole) => void
  clearRole: () => void
  /** true once localStorage has been read on mount -- avoids a flash of "no role" during hydration */
  ready: boolean
}

const RoleContext = createContext<RoleContextValue | undefined>(undefined)

export function RoleProvider({ children }: { children: ReactNode }) {
  const [role, setRoleState] = useState<UserRole | null>(null)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY)
      if (stored === 'credit_ops_l1' || stored === 'credit_ops_l2' || stored === 'credit_head') {
        setRoleState(stored)
      }
    } catch {
      // localStorage unavailable (private browsing, etc.) -- fall back to session-only role state
    }
    setReady(true)
  }, [])

  const setRole = (next: UserRole) => {
    setRoleState(next)
    try {
      window.localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // ignore -- role still works for this session via React state
    }
  }

  const clearRole = () => {
    setRoleState(null)
    try {
      window.localStorage.removeItem(STORAGE_KEY)
    } catch {
      // ignore
    }
  }

  return <RoleContext.Provider value={{ role, setRole, clearRole, ready }}>{children}</RoleContext.Provider>
}

export function useRole(): RoleContextValue {
  const ctx = useContext(RoleContext)
  if (!ctx) throw new Error('useRole must be used within a RoleProvider')
  return ctx
}
