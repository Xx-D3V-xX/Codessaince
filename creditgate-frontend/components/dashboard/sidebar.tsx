'use client'

import {
  AlertTriangle, ArrowUpRight, ChevronDown, CircleHelp, ClipboardCheck, Database,
  FileSearch, GitBranch, Home, MoreHorizontal, Settings2, UsersRound, X,
} from 'lucide-react'
import { ROLE_LABELS, useRole } from '@/lib/role-context'

export const NAV_ITEMS = [
  { key: 'overview', label: 'Overview', icon: Home },
  { key: 'applications', label: 'Applications', icon: ClipboardCheck },
  { key: 'rules', label: 'Rule engine', icon: GitBranch },
  { key: 'exceptions', label: 'Exceptions', icon: AlertTriangle },
  { key: 'audit', label: 'Audit trail', icon: FileSearch },
] as const

export type NavKey = (typeof NAV_ITEMS)[number]['key']

export function Sidebar({
  active,
  setActive,
  open,
  setOpen,
  exceptionCount,
}: {
  active: NavKey
  setActive: (v: NavKey) => void
  open: boolean
  setOpen: (v: boolean) => void
  exceptionCount: number | null
}) {
  const { role } = useRole()

  return (
    <aside className={`sidebar ${open ? 'sidebar-open' : ''}`}>
      <div className="sidebar-top">
        <div className="brand-mark" aria-label="Credit score mark">
          <svg viewBox="0 0 32 32" role="img" aria-hidden="true">
            <path d="M7 17a9 9 0 1 1 3 6.7" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
            <path d="M7 17h8M7 17l5-5" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
            <circle cx="23.5" cy="22.5" r="3.5" fill="currentColor" />
          </svg>
        </div>
        <div className="brand-copy">
          <strong>CreditGate</strong>
          <span>Underwriting OS</span>
        </div>
        <button className="icon-button sidebar-close" onClick={() => setOpen(false)} aria-label="Close navigation">
          <X />
        </button>
      </div>

      <div className="workspace-switcher">
        <div className="workspace-icon">N</div>
        <div>
          <strong>Northstar Capital</strong>
          <span>{role ? ROLE_LABELS[role] : 'No role selected'}</span>
        </div>
        <ChevronDown />
      </div>

      <nav className="nav-list">
        <span className="nav-label">Workspace</span>
        {NAV_ITEMS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            className={`nav-item ${active === key ? 'active' : ''}`}
            onClick={() => { setActive(key); setOpen(false) }}
          >
            <Icon />
            <span>{label}</span>
            {key === 'exceptions' && exceptionCount !== null && exceptionCount > 0 && <b>{exceptionCount}</b>}
          </button>
        ))}
        <span className="nav-label nav-label-spaced">Manage</span>
        <button className="nav-item" disabled>
          <UsersRound />
          <span>Team &amp; access</span>
        </button>
        <button className="nav-item" disabled>
          <Settings2 />
          <span>Settings</span>
        </button>
      </nav>

      <div className="sidebar-bottom">
        <div className="help-card">
          <CircleHelp />
          <div>
            <strong>Need a hand?</strong>
            <span>Read the playbook</span>
          </div>
          <ArrowUpRight />
        </div>
        <div className="user-row">
          <div className="avatar">{role ? role.slice(-2).toUpperCase() : '--'}</div>
          <div>
            <strong>{role ? ROLE_LABELS[role] : 'Guest'}</strong>
            <span>Risk operations</span>
          </div>
          <MoreHorizontal />
        </div>
      </div>
    </aside>
  )
}
