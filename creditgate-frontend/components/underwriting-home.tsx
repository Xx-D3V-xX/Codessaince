'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Script from 'next/script'
import { ArrowRight, Check, ChevronDown, FileText, LockKeyhole, Menu, ShieldCheck, Sparkles, X, Zap } from 'lucide-react'
import { ALL_ROLES, ROLE_LABELS, useRole } from '@/lib/role-context'
import type { UserRole } from '@/lib/types'

const features = [
  { icon: FileText, eyebrow: '01 / Intake', title: 'Every document, one clear view', copy: 'Ingest bank statements, GST returns, bureau data and more without the spreadsheet chase.', className: 'md:col-span-2' },
  { icon: Sparkles, eyebrow: '02 / Intelligence', title: 'See repayment capacity in context', copy: 'Normalize fragmented financial signals into a decision-ready borrower profile.', className: '' },
  { icon: Zap, eyebrow: '03 / Automation', title: 'Approve the obvious. Escalate the nuanced.', copy: 'Rules and models route each application to the right next step, instantly.', className: '' },
  { icon: ShieldCheck, eyebrow: '04 / Governance', title: 'Exceptions with accountability', copy: 'Capture rationale, authority and conditions for every commercial exception.', className: 'md:col-span-2' },
]

const ROLE_DESCRIPTIONS: Record<UserRole, string> = {
  credit_ops_l1: 'Resolve L1 exceptions and review the standard underwriting queue.',
  credit_ops_l2: 'Resolve L2 exceptions -- higher loan amounts, larger deviations.',
  credit_head: 'Full authority. Can act on any exception level, L1 through Credit Head.',
}

export default function UnderwritingHome() {
  const [authOpen, setAuthOpen] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [pendingRole, setPendingRole] = useState<UserRole | null>(null)
  const router = useRouter()
  const { setRole } = useRole()

  const openAuth = () => { setAuthOpen(true); setMenuOpen(false) }

  const enterWorkspace = () => {
    if (!pendingRole) return
    setRole(pendingRole)
    router.push('/dashboard')
  }

  return (
    <main className="min-h-screen overflow-hidden bg-background text-foreground">
      <Script src="https://cdn.spline.design/@splinetool/viewer@2.0.2/build/spline-viewer.js" type="module" />
      <header className="fixed inset-x-0 top-0 z-40 border-b border-white/10 bg-background/70 backdrop-blur-xl">
        <div className="mx-auto flex h-20 max-w-7xl items-center justify-between px-6 lg:px-10">
          <a href="#top" className="font-serif text-2xl italic tracking-tight">CreditGate<span className="text-primary">.</span></a>
          <nav className="hidden items-center gap-8 text-sm text-muted-foreground md:flex">
            <a href="#how-it-works" className="transition-colors hover:text-foreground">How it works</a>
            <a href="#features" className="transition-colors hover:text-foreground">Features</a>
            <a href="#decisions" className="transition-colors hover:text-foreground">Risk decisions</a>
            <a href="#contact" className="transition-colors hover:text-foreground">Contact</a>
          </nav>
          <div className="hidden items-center gap-5 md:flex">
            <button onClick={openAuth} className="text-sm text-muted-foreground transition-colors hover:text-foreground">Log in</button>
            <button onClick={openAuth} className="rounded-full bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground transition-transform hover:scale-105">Get started</button>
          </div>
          <button aria-label="Open menu" onClick={() => setMenuOpen(!menuOpen)} className="md:hidden"><Menu size={22} /></button>
        </div>
        {menuOpen && <div className="border-t border-white/10 bg-background px-6 py-5 md:hidden"><nav className="flex flex-col gap-5 text-sm text-muted-foreground"><a href="#how-it-works" onClick={() => setMenuOpen(false)}>How it works</a><a href="#features" onClick={() => setMenuOpen(false)}>Features</a><a href="#decisions" onClick={() => setMenuOpen(false)}>Risk decisions</a><button onClick={openAuth} className="w-fit rounded-full bg-primary px-5 py-2.5 text-primary-foreground">Log in / Get started</button></nav></div>}
      </header>

      <section id="top" className="relative isolate overflow-hidden px-4 pb-10 pt-24 sm:px-6 lg:px-10">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_76%_42%,rgba(138,158,185,0.16),transparent_38%),radial-gradient(ellipse_at_12%_80%,rgba(77,102,128,0.12),transparent_32%)]" />
        <div className="relative mx-auto grid min-h-[620px] w-full max-w-7xl items-center gap-4 overflow-hidden rounded-[2.25rem] border border-white/15 bg-white/[0.045] px-6 py-8 shadow-2xl shadow-black/20 backdrop-blur-2xl sm:px-10 lg:grid-cols-[0.8fr_1.2fr] lg:px-12 lg:py-10">
          <div className="relative z-10 max-w-xl">
            <p className="mb-6 font-mono text-xs uppercase tracking-[0.25em] text-primary">Credit intelligence, without the noise</p>
            <h1 className="text-balance text-5xl font-semibold leading-[1.05] tracking-[-0.04em] sm:text-6xl lg:text-7xl">Make every lending decision <span className="text-primary">defensible.</span></h1>
            <p className="mt-7 max-w-lg text-pretty text-lg leading-8 text-muted-foreground">CreditGate turns messy financial applications into clear, consistent decisions, from instant approvals to governed exceptions.</p>
            <div className="mt-9 flex flex-wrap items-center gap-4"><button onClick={openAuth} className="group flex items-center gap-3 rounded-full bg-primary px-6 py-3.5 text-sm font-medium text-primary-foreground">Start evaluating <ArrowRight size={17} className="transition-transform group-hover:translate-x-1" /></button><a href="#how-it-works" className="flex items-center gap-2 rounded-full border border-white/15 px-6 py-3.5 text-sm text-muted-foreground transition-colors hover:border-white/30 hover:text-foreground">See how it works <ChevronDown size={16} /></a></div>
            <div className="mt-12 flex gap-8 text-xs text-muted-foreground"><span className="flex items-center gap-2"><Check size={14} className="text-primary" /> Built for NBFC teams</span><span className="flex items-center gap-2"><Check size={14} className="text-primary" /> Audit-ready by design</span></div>
          </div>
          <div className="relative -mr-16 h-[600px] overflow-visible lg:h-[720px]"><spline-viewer url="https://prod.spline.design/EZh0UIKFSEeXVjYP/scene.splinecode" className="absolute inset-0 h-full w-full" /></div>
        </div>
      </section>

      <section id="how-it-works" className="relative overflow-hidden px-6 py-16 lg:px-10 lg:py-20"><div className="mx-auto max-w-7xl"><div className="mb-12 text-center"><p className="font-mono text-xs uppercase tracking-[0.25em] text-primary">One flow. Every signal.</p><h2 className="mt-5 text-4xl font-semibold tracking-[-0.03em] sm:text-5xl">From application to answer.</h2><p className="mx-auto mt-6 max-w-xl leading-7 text-muted-foreground">Bring your team into one shared decision layer. CreditGate gives analysts the context to move faster and leaders the control to stay consistent.</p></div><div className="grid items-center gap-4 lg:grid-cols-[0.7fr_1.5fr_0.7fr]"><div className="order-2 grid gap-4 lg:order-1"><article className="rounded-2xl border border-white/10 bg-card/40 p-5 text-left backdrop-blur-xl"><p className="font-mono text-[10px] uppercase tracking-[0.18em] text-primary">Signal stack</p><h3 className="mt-3 text-lg font-medium">One borrower view</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">Statements, bureau and GST data, normalized together.</p></article><article className="rounded-2xl border border-white/10 bg-card/40 p-5 text-left backdrop-blur-xl"><p className="font-mono text-[10px] uppercase tracking-[0.18em] text-primary">Controls</p><h3 className="mt-3 text-lg font-medium">Policy-aware routing</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">Every case reaches the right reviewer with context.</p></article></div><div className="relative order-1 mx-auto h-[470px] w-full max-w-3xl overflow-hidden rounded-[2rem] border border-white/10 bg-card/25 shadow-2xl shadow-black/20 backdrop-blur-xl lg:order-2 lg:h-[620px]"><iframe src="https://my.spline.design/globaltransactions-Te6PpNvKN89250qMirtLkitF/" title="Global transaction intelligence visualization" className="h-full w-full border-0" /></div><div className="order-3 grid gap-4"><article className="rounded-2xl border border-white/10 bg-card/40 p-5 text-left backdrop-blur-xl"><p className="font-mono text-[10px] uppercase tracking-[0.18em] text-primary">Outcomes</p><h3 className="mt-3 text-lg font-medium">Clear next steps</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">Approve, reject or escalate with confidence.</p></article><article className="rounded-2xl border border-white/10 bg-card/40 p-5 text-left backdrop-blur-xl"><p className="font-mono text-[10px] uppercase tracking-[0.18em] text-primary">Evidence</p><h3 className="mt-3 text-lg font-medium">Audit-ready trails</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">Decisions that stay explainable long after the close.</p></article></div></div></div></section>

      <section id="features" className="px-6 py-16 lg:px-10 lg:py-20"><div className="mx-auto max-w-7xl"><div className="mb-12 max-w-2xl"><p className="font-mono text-xs uppercase tracking-[0.25em] text-primary">The decision layer</p><h2 className="mt-5 text-4xl font-semibold tracking-[-0.03em] sm:text-5xl">A sharper view of risk.</h2></div><div className="grid gap-4 md:grid-cols-2">{features.map(({ icon: Icon, eyebrow, title, copy, className }) => <article key={title} className={`group rounded-[1.5rem] border border-white/10 bg-card/45 p-7 backdrop-blur-xl transition-colors hover:border-white/25 ${className}`}><div className="mb-14 flex items-center justify-between"><div className="flex h-10 w-10 items-center justify-center rounded-xl border border-white/10 bg-white/5 text-primary"><Icon size={19} /></div><span className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground">{eyebrow}</span></div><h3 className="max-w-md text-2xl font-medium tracking-tight">{title}</h3><p className="mt-3 max-w-md leading-7 text-muted-foreground">{copy}</p></article>)}</div></div></section>

      <section id="decisions" className="px-6 pb-24 lg:px-10 lg:pb-32"><div className="bg-primary mx-auto flex max-w-7xl flex-col items-start justify-between gap-8 rounded-[2rem] border border-white/10 p-8 text-primary-foreground sm:p-12 lg:flex-row lg:items-end"><div><p className="inline-flex rounded-full px-3 py-1 font-mono text-xs uppercase tracking-[0.25em] text-black">Ready when you are</p><h2 className="mt-5 max-w-2xl text-4xl font-semibold tracking-[-0.03em] sm:text-5xl">Make the next decision your best one.</h2></div><button onClick={openAuth} className="flex shrink-0 items-center gap-3 rounded-full bg-background px-6 py-3.5 text-sm font-medium text-foreground">Open CreditGate <ArrowRight size={17} /></button></div></section>

      <footer id="contact" className="border-t border-white/10 px-6 py-8 lg:px-10"><div className="mx-auto flex max-w-7xl flex-col justify-between gap-4 text-sm text-muted-foreground sm:flex-row"><span className="font-serif text-lg italic text-foreground">CreditGate<span className="text-primary">.</span></span><span>Decision infrastructure for modern lending.</span><span>&copy; 2026 CreditGate</span></div></footer>

      {authOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-md">
          <div className="relative grid max-h-[90vh] w-full max-w-5xl overflow-hidden rounded-[2rem] border border-white/15 bg-card shadow-2xl lg:grid-cols-2">
            <button aria-label="Close" onClick={() => { setAuthOpen(false); setPendingRole(null) }} className="absolute right-5 top-5 z-10 rounded-full border border-white/10 bg-background/70 p-2 text-muted-foreground hover:text-foreground"><X size={18} /></button>
            <div className="flex flex-col justify-center p-8 sm:p-12">
              <div className="mb-8">
                <div className="mb-8 flex h-11 w-11 items-center justify-center rounded-xl bg-primary text-primary-foreground"><LockKeyhole size={19} /></div>
                <p className="font-mono text-xs uppercase tracking-[0.22em] text-primary">Welcome to CreditGate</p>
                <h2 className="mt-4 text-4xl font-semibold tracking-tight">Choose your role.</h2>
                <p className="mt-3 leading-6 text-muted-foreground">
                  This build has no real sign-in yet -- select which underwriting role you&apos;re working as. Your choice is sent as the <code className="rounded bg-background/60 px-1.5 py-0.5 text-xs">X-User-Role</code> header on exception approvals, so it determines what you&apos;re actually authorized to resolve.
                </p>
              </div>

              <div className="space-y-3" role="radiogroup" aria-label="Select role">
                {ALL_ROLES.map((r) => {
                  const selected = pendingRole === r
                  return (
                    <button
                      key={r}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      onClick={() => setPendingRole(r)}
                      className={`w-full rounded-xl border px-4 py-3.5 text-left transition-colors ${
                        selected ? 'border-primary bg-primary/10' : 'border-white/10 bg-background/60 hover:border-white/25'
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium text-foreground">{ROLE_LABELS[r]}</span>
                        <span
                          className={`flex h-4 w-4 items-center justify-center rounded-full border ${
                            selected ? 'border-primary bg-primary' : 'border-white/25'
                          }`}
                        >
                          {selected && <Check size={11} className="text-primary-foreground" />}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">{ROLE_DESCRIPTIONS[r]}</p>
                    </button>
                  )
                })}
              </div>

              <button
                onClick={enterWorkspace}
                disabled={!pendingRole}
                className="mt-6 w-full rounded-xl bg-primary px-4 py-3.5 text-sm font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-40"
              >
                Continue to workspace
              </button>
              <p className="mt-6 text-center text-xs text-muted-foreground">No credentials are sent anywhere -- this is a client-side role selector, not authentication.</p>
            </div>
            <div className="hidden min-h-[500px] border-l border-white/10 bg-background/40 lg:block"><iframe src="https://my.spline.design/globaltransactions-Te6PpNvKN89250qMirtLkitF/" title="Transaction visualization" className="h-full w-full border-0" /></div>
          </div>
        </div>
      )}
    </main>
  )
}
