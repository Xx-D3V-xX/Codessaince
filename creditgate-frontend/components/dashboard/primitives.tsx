'use client'

import type { ReactNode } from 'react'

/** maps a free-text status string onto the CSS's three pill variants (approved/review/exception) */
function pillVariant(status: string): 'approved' | 'review' | 'exception' {
  const s = status.toLowerCase()
  if (s.includes('reject') || s.includes('fail') || s.includes('exception')) return 'exception'
  if (s.includes('review') || s.includes('pending') || s.includes('processing') || s.includes('insufficient')) return 'review'
  return 'approved'
}

export function Pill({ status, label }: { status: string; label?: string }) {
  const variant = pillVariant(status)
  return (
    <span className={`status-pill status-${variant}`}>
      <i className="status-dot" />
      {label ?? status}
    </span>
  )
}

/**
 * Three-state badge for RuleTraceEntry.condition_met -- true=fired, false=clear,
 * null=couldn't evaluate. NEVER coerce null to false; it renders as its own
 * distinct "Unknown" state, per the backend's explicit contract.
 */
export function ConditionBadge({ conditionMet }: { conditionMet: boolean | null }) {
  if (conditionMet === true) return <Pill status="Exception" label="Fired" />
  if (conditionMet === false) return <Pill status="Approved" label="Clear" />
  return <Pill status="Review" label="Unknown" />
}

export function EmptyState({ title, description, icon }: { title: string; description: string; icon?: ReactNode }) {
  return (
    <div className="empty-state panel bento-cream">
      {icon && <div className="brand-mark">{icon}</div>}
      <h2>{title}</h2>
      <p>{description}</p>
    </div>
  )
}

export function ErrorNotice({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="exception-banner" role="alert">
      <div>
        <strong>Something went wrong</strong>
        <span>{message}</span>
      </div>
      {onRetry && (
        <button className="primary-button" onClick={onRetry} type="button">
          Retry
        </button>
      )}
    </div>
  )
}

export function LoadingRow({ label = 'Loading\u2026' }: { label?: string }) {
  return (
    <div className="footer-note">
      <span>
        <span className="live-dot" /> {label}
      </span>
    </div>
  )
}
