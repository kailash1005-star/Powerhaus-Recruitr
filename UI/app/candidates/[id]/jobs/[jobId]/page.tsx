'use client';

import { useParams } from 'next/navigation';
import { PipelineJobCandidatesPage } from '@/components/pages/PipelineJobCandidatesPage';

export default function PipelineJobCandidatesRoute() {
  const params = useParams();
  const id = params.id as string;
  const jobId = params.jobId as string;
  return <PipelineJobCandidatesPage pipelineId={id} jobId={jobId} />;
}
