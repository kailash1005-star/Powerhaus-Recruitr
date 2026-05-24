'use client';

import { useParams } from 'next/navigation';
import { RunDetailPage } from '@/components/pages/RunDetailPage';

export default function RunDetailRoute() {
  const params = useParams();
  const id = params.id as string;
  return <RunDetailPage runId={id} />;
}
