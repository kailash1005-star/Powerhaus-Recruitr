'use client';

import { Suspense } from 'react';
import { ICPConfigPage } from '@/components/pages/ICPConfigPage';

export default function ICPRoute() {
  return (
    <Suspense>
      <ICPConfigPage />
    </Suspense>
  );
}
