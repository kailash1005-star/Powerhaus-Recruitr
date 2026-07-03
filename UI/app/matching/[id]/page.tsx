'use client';

import { useParams } from 'next/navigation';
import { MatchingRunDetailPage } from '@/components/pages/MatchingRunDetailPage';

export default function MatchingRunRoute() {
  const params = useParams();
  const id = params.id as string;
  return <MatchingRunDetailPage runId={id} />;
}
