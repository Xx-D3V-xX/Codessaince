import type { DetailedHTMLProps, HTMLAttributes } from 'react'

/**
 * <spline-viewer> is a custom element loaded at runtime via the
 * <Script src="https://cdn.spline.design/.../spline-viewer.js"> tag in
 * underwriting-home.tsx -- it has no official React/TS types. This was
 * missing from the original digi-pay shell too (tsc failed on it there as
 * well); declared here so the marketing homepage type-checks.
 *
 * With "jsx": "react-jsx" (the automatic runtime), intrinsic elements are
 * resolved through React's own JSX namespace, not a bare global one -- so
 * this augments 'react' rather than declaring `global { namespace JSX }`.
 */
declare module 'react' {
  namespace JSX {
    interface IntrinsicElements {
      'spline-viewer': DetailedHTMLProps<HTMLAttributes<HTMLElement>, HTMLElement> & {
        url?: string
      }
    }
  }
}

export {}
