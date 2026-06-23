'use client';

import { Suspense } from 'react';
import { AgentChatPage } from '@/components/pages/AgentChatPage';

export default function AgentRoute() {
  return (
    <Suspense>
      <AgentChatPage />
    </Suspense>
  );
}
