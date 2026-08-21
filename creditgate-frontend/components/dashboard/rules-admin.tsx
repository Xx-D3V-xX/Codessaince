'use client'

import { useEffect, useState } from 'react'
import { Check, GitBranch, Layers, RefreshCw, X } from 'lucide-react'
import { ApiError, listRules, patchRule } from '@/lib/api'
import type { ApplicantPipeline, RuleResponse } from '@/lib/types'
import { useRole } from '@/lib/role-context'
import { Pill, ErrorNotice } from './primitives'

/**
 * Rule.value is always a dict, e.g. {"threshold": 700} or {"threshold": true},
 * never a raw scalar (src/db/models.py: "numeric/string/list, operator-dependent").
 * The seeded rules in this project consistently use a single "threshold" key,
 * so the editor exposes that key specifically rather than a generic JSON blob --
 * simpler for the live-demo threshold-edit flow, and if a rule's value dict ever
 * uses a different key shape, raw JSON is still there as the escape hatch below.
 */
function extractThresholdValue(value: Record<string, unknown>): string {
  if ('threshold' in value) return JSON.stringify(value.threshold)
  return JSON.stringify(value)
}

function buildValuePatch(original: Record<string, unknown>, rawInput: string): Record<string, unknown> {
  let parsed: unknown
  try {
    parsed = JSON.parse(rawInput)
  } catch {
    parsed = rawInput // not valid JSON -- treat as a raw string threshold
  }
  if ('threshold' in original) return { ...original, threshold: parsed }
  return typeof parsed === 'object' && parsed !== null ? (parsed as Record<string, unknown>) : { threshold: parsed }
}

export function RulesAdmin() {
  const [pipeline, setPipeline] = useState<ApplicantPipeline>('INDIVIDUAL')
  const [rules, setRules] = useState<RuleResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editingCode, setEditingCode] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const { role } = useRole()

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const res = await listRules(pipeline)
      setRules(res)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Could not load rules.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pipeline])

  function startEdit(rule: RuleResponse) {
    setEditingCode(rule.rule_code)
    setEditValue(extractThresholdValue(rule.value))
    setSaveError(null)
  }

  async function saveEdit(rule: RuleResponse) {
    setSaving(true)
    setSaveError(null)
    try {
      const updated = await patchRule(rule.rule_code, {
        edited_by: role ?? 'unknown',
        value: buildValuePatch(rule.value, editValue),
      })
      setRules((prev) => prev.map((r) => (r.rule_code === updated.rule_code ? updated : r)))
      setEditingCode(null)
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.detail : 'Could not save that edit.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Rule engine</h1>
          <p>Live, versioned thresholds. Editing here is what &quot;Re-run decision&quot; on the Decision panel reacts to.</p>
        </div>
        <div className="heading-actions">
          <div className="range-toggle">
            {(['INDIVIDUAL', 'MSME'] as ApplicantPipeline[]).map((p) => (
              <button key={p} className={pipeline === p ? 'selected' : ''} onClick={() => setPipeline(p)} type="button">
                {p}
              </button>
            ))}
          </div>
          <button className="ghost-button" onClick={load} type="button">
            <RefreshCw /> Refresh
          </button>
        </div>
      </div>

      {error && <ErrorNotice message={error} onRetry={load} />}

      {loading && !error ? (
        <div className="footer-note"><span><span className="live-dot" /> Loading rules\u2026</span></div>
      ) : (
        <section className="panel rule-list bento-cream" style={{ width: '100%' }}>
          <div className="panel-header">
            <div>
              <h2>Active rules \u2014 {pipeline}</h2>
              <p>Sorted by priority. Compound conditions share a rule_group (implicitly AND&apos;d).</p>
            </div>
            <span className="icon-badge"><Layers /></span>
          </div>

          {rules.map((rule) => {
            const isEditing = editingCode === rule.rule_code
            return (
              <div className="rule-row" key={rule.id} style={{ alignItems: isEditing ? 'flex-start' : 'center' }}>
                <div className="rule-icon"><GitBranch /></div>
                <div style={{ flex: 1 }}>
                  <strong>{rule.rule_code}</strong>
                  <span>
                    {rule.field} {rule.operator} \u00b7 v{rule.version} \u00b7 {rule.outcome}
                    {rule.severity ? ` / ${rule.severity}` : ''} \u00b7 priority {rule.priority} \u00b7 group {rule.rule_group}
                  </span>
                  {isEditing ? (
                    <div style={{ marginTop: 10, display: 'flex', gap: 8, alignItems: 'center' }}>
                      <input
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        style={{ padding: '7px 10px', borderRadius: 7, border: '1px solid #b7b4b1', fontSize: 11, width: 160 }}
                        autoFocus
                      />
                      <button className="icon-button" onClick={() => saveEdit(rule)} disabled={saving} type="button" aria-label="Save">
                        {saving ? <RefreshCw className="spin" /> : <Check />}
                      </button>
                      <button className="icon-button" onClick={() => setEditingCode(null)} disabled={saving} type="button" aria-label="Cancel">
                        <X />
                      </button>
                    </div>
                  ) : (
                    saveError && editingCode === null && null
                  )}
                </div>
                {!isEditing && (
                  <>
                    <Pill status={rule.outcome === 'HARD_REJECT' ? 'Exception' : 'Review'} label={JSON.stringify(rule.value)} />
                    <button className="ghost-button" onClick={() => startEdit(rule)} type="button" style={{ padding: '7px 10px' }}>
                      Edit
                    </button>
                  </>
                )}
              </div>
            )
          })}

          {saveError && (
            <div style={{ padding: '0 19px 16px' }}>
              <ErrorNotice message={saveError} />
            </div>
          )}

          {!loading && rules.length === 0 && !error && (
            <div style={{ padding: '20px 19px', fontSize: 11, color: '#666361' }}>No active rules for {pipeline}.</div>
          )}
        </section>
      )}
    </>
  )
}
