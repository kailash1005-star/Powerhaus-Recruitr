'use client';

import { useParams } from 'next/navigation';
import { RunResultsPage } from '@/components/pages/RunResultsPage';

export default function RunResultsRoute() {
  const params = useParams();
  const id = params.id as string;
  return <RunResultsPage runId={id} />;
}
