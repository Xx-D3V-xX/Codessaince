'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { Bell, ChevronDown, LogOut, Menu } from 'lucide-react'
import { ALL_ROLES, ROLE_LABELS, useRole } from '@/lib/role-context'
import type { NavKey } from './sidebar'

const LABELS: Record<NavKey, string> = {
  overview: 'Overview',
  applications: 'Applications',
  rules: 'Rule engine',
  exceptions: 'Exceptions',
  audit: 'Audit trail',
}

export function Header({ active, setOpen }: { active: NavKey; setOpen: (v: boolean) => void }) {
  const [roleMenuOpen, setRoleMenuOpen] = useState(false)
  const { role, setRole, clearRole } = useRole()
  const router = useRouter()

  return (
    <header className="topbar">
      <button className="icon-button mobile-menu" onClick={() => setOpen(true)} aria-label="Open navigation">
        <Menu />
      </button>
      <div className="breadcrumbs">
        <span>Workspace</span>
        <span>/</span>
        <strong>{LABELS[active]}</strong>
      </div>
      <div className="topbar-actions" style={{ position: 'relative', gap: 14 }}>
        <button
          className="date-button"
          onClick={() => setRoleMenuOpen((v) => !v)}
          type="button"
          aria-haspopup="listbox"
          aria-expanded={roleMenuOpen}
        >
          {role ? ROLE_LABELS[role] : 'Select role'} <ChevronDown />
        </button>
        {roleMenuOpen && (
          <div
            style={{
              position: 'absolute', top: '110%', right: 90, background: '#b9e4f0', border: '1px solid #b7b4b1',
              borderRadius: 10, padding: 6, minWidth: 200, zIndex: 30, boxShadow: '0 12px 24px -12px rgba(0,0,0,.5)',
            }}
            role="listbox"
          >
            {ALL_ROLES.map((r) => (
              <button
                key={r}
                type="button"
                role="option"
                aria-selected={role === r}
                onClick={() => { setRole(r); setRoleMenuOpen(false) }}
                style={{
                  display: 'block', width: '100%', textAlign: 'left', padding: '8px 10px', borderRadius: 7,
                  fontSize: 11, color: '#171314', background: role === r ? '#a9dfed' : 'transparent', border: 0, cursor: 'pointer',
                }}
              >
                {ROLE_LABELS[r]}
              </button>
            ))}
            <button
              type="button"
              onClick={() => { clearRole(); setRoleMenuOpen(false); router.push('/') }}
              style={{
                display: 'flex', alignItems: 'center', gap: 6, width: '100%', textAlign: 'left', padding: '8px 10px',
                borderRadius: 7, fontSize: 11, color: '#a4453f', background: 'transparent', border: 0, cursor: 'pointer', marginTop: 4,
              }}
            >
              <LogOut size={12} /> Exit workspace
            </button>
          </div>
        )}
        <button className="icon-button">
          <Bell />
          <i />
        </button>
        <div className="avatar small">{role ? role.slice(-2).toUpperCase() : '--'}</div>
      </div>
    </header>
  )
}
