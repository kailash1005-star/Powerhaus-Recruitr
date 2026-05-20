import type { Metadata } from 'next'
import { Inter, JetBrains_Mono } from 'next/font/google'
import { Analytics } from '@vercel/analytics/next'
import './globals.css'
import { AppShell } from '@/components/leadgen/app-shell'

const inter = Inter({ 
  subsets: ["latin"],
  variable: '--font-inter',
});

const jetbrainsMono = JetBrains_Mono({ 
  subsets: ["latin"],
  variable: '--font-jetbrains-mono',
});

export const metadata: Metadata = {
  title: 'LeadGen Agent',
  description: 'AI-Powered Lead Generation Platform',
  generator: 'v0.app',
  icons: {
    icon: [
      {
        url: '/image.png',
        media: '(prefers-color-scheme: light)',
      },
      {
        url: '/image.png',
        media: '(prefers-color-scheme: dark)',
      },
      {
        url: '/image.png',
        type: 'image/svg+xml',
      },
    ],
    apple: '/image.png',
  },
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en">
      <body className={`${inter.variable} ${jetbrainsMono.variable} font-sans antialiased`}>
        <AppShell>{children}</AppShell>
        <Analytics />
      </body>
    </html>
  )
}
