'use client';

import { Suspense } from 'react';
import { RunsPage } from '@/components/pages/RunsPage';

export default function RunsRoute() {
  return (
    <Suspense>
      <RunsPage />
    </Suspense>
  );
}
