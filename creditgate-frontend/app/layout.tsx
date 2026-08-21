import { Analytics } from '@vercel/analytics/next'
import { Geist, Geist_Mono, Instrument_Serif, Inter, JetBrains_Mono, Plus_Jakarta_Sans } from 'next/font/google'
import type { Metadata, Viewport } from 'next'
import './globals.css'
import { RoleProvider } from '@/lib/role-context'

// digi-pay (marketing) fonts
const geist = Geist({ subsets: ['latin'], variable: '--font-geist' })
const geistMono = Geist_Mono({ subsets: ['latin'], variable: '--font-geist-mono' })
const instrumentSerif = Instrument_Serif({ subsets: ['latin'], weight: '400', variable: '--font-instrument' })

// credit-gate (dashboard) fonts
const plusJakarta = Plus_Jakarta_Sans({ subsets: ['latin'], variable: '--font-geist-sans', weight: ['600', '700', '800'] })
const inter = Inter({ subsets: ['latin'], variable: '--font-inter', weight: ['400', '500', '600'] })
const jetbrainsMono = JetBrains_Mono({ subsets: ['latin'], variable: '--font-jetbrains-mono', weight: ['400', '500'] })

export const metadata: Metadata = {
  title: 'CreditGate | Underwriting OS',
  description: 'A clear, explainable command center for modern credit underwriting teams.',
  icons: {
    icon: [
      { url: '/icon-light-32x32.png', media: '(prefers-color-scheme: light)' },
      { url: '/icon-dark-32x32.png', media: '(prefers-color-scheme: dark)' },
      { url: '/icon.svg', type: 'image/svg+xml' },
    ],
    apple: '/apple-icon.png',
  },
}

export const viewport: Viewport = {
  colorScheme: 'light dark',
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: 'white' },
    { media: '(prefers-color-scheme: dark)', color: 'black' },
  ],
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${geist.variable} ${geistMono.variable} ${instrumentSerif.variable} ${plusJakarta.variable} ${inter.variable} ${jetbrainsMono.variable} bg-background`}
    >
      <body className="antialiased">
        <RoleProvider>{children}</RoleProvider>
        {process.env.NODE_ENV === 'production' && <Analytics />}
      </body>
    </html>
  )
}
