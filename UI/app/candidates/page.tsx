'use client';

import { Suspense } from 'react';
import { PipelinesPage } from '@/components/pages/PipelinesPage';

export default function PipelinesRoute() {
  return (
    <Suspense>
      <PipelinesPage />
    </Suspense>
  );
}
