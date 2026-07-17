import type { Metadata } from 'next';
import './globals.css';
import { ShellGate } from '@/components/ShellGate';

export const metadata: Metadata = {
  title: 'Recruitr',
  description: 'AI-powered recruitment lead generation platform',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <ShellGate>{children}</ShellGate>
      </body>
    </html>
  );
}
