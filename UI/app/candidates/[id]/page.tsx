'use client';

import { useParams } from 'next/navigation';
import { PipelineDetailPage } from '@/components/pages/PipelineDetailPage';

export default function PipelineDetailRoute() {
  const params = useParams();
  const id = params.id as string;
  return <PipelineDetailPage pipelineId={id} />;
}
