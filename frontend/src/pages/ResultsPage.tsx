import { useEffect, useState, useCallback } from 'react';
import { getSearchResult } from '../services/api';
import type { SearchResult } from '../services/api';
import { useWebSocket } from '../hooks/useWebSocket';
import ActivityFeed from '../components/ActivityFeed';
import ContactCard from '../components/ContactCard';

interface Props {
  jobId: string;
  onBack: () => void;
}

const STATUS_LABELS: Record<string, string> = {
  pending: 'Starting...',
  finding_people: 'Finding contacts...',
  finding_emails: 'Discovering emails...',
  researching: 'Researching company...',
  generating_emails: 'Writing emails...',
  completed: 'Complete',
  failed: 'Failed',
};

const STEP_ORDER = [
  'pending',
  'finding_people',
  'finding_emails',
  'researching',
  'generating_emails',
  'completed',
];

export default function ResultsPage({ jobId, onBack }: Props) {
  const [result, setResult] = useState<SearchResult | null>(null);
  const { data: wsData } = useWebSocket(jobId);

  // Prefer WebSocket data, fall back to polling
  const displayResult = wsData || result;

  // Polling fallback
  const poll = useCallback(async () => {
    try {
      const data = await getSearchResult(jobId);
      setResult(data);
    } catch {
      // ignore polling errors
    }
  }, [jobId]);

  useEffect(() => {
    poll();
    const interval = setInterval(poll, 3000);
    return () => clearInterval(interval);
  }, [poll]);

  if (!displayResult) {
    return (
      <div className="min-h-screen bg-gray-950 text-white flex items-center justify-center">
        <div className="animate-pulse text-gray-400">Loading...</div>
      </div>
    );
  }

  const status = displayResult.status;
  const isRunning = !['completed', 'failed'].includes(status);
  const stepIndex = STEP_ORDER.indexOf(status);
  const progress = Math.max(0, Math.min(100, (stepIndex / (STEP_ORDER.length - 1)) * 100));

  // Match email results and drafts to people by name
  function getEmailForPerson(name: string) {
    return displayResult!.email_results.find((e) => e.name === name);
  }
  function getDraftForPerson(name: string) {
    return displayResult!.email_drafts.find((d) => d.name === name);
  }

  return (
    <div className="min-h-screen bg-gray-950 text-white">
      <div className="max-w-3xl mx-auto px-4 py-8 space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <button
              onClick={onBack}
              className="text-sm text-gray-500 hover:text-gray-300 mb-2 cursor-pointer"
            >
              &larr; New search
            </button>
            <h1 className="text-2xl font-bold">
              {displayResult.company} &mdash; {displayResult.role}
            </h1>
          </div>
          <span className={`text-sm px-3 py-1 rounded-full border ${
            status === 'completed' ? 'border-green-700 text-green-400' :
            status === 'failed' ? 'border-red-700 text-red-400' :
            'border-blue-700 text-blue-400'
          }`}>
            {STATUS_LABELS[status] || status}
          </span>
        </div>

        {/* Progress bar */}
        {isRunning && (
          <div className="w-full h-1 bg-gray-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-blue-500 rounded-full transition-all duration-500"
              style={{ width: `${progress}%` }}
            />
          </div>
        )}

        {/* Activity feed */}
        <ActivityFeed entries={displayResult.activity_log} status={status} />

        {/* Error */}
        {displayResult.error && (
          <div className="rounded-xl bg-red-950/30 border border-red-800 p-4 text-red-300 text-sm">
            {displayResult.error}
          </div>
        )}

        {/* Results */}
        {displayResult.people.length > 0 && (
          <div className="space-y-3">
            <h2 className="text-lg font-semibold text-gray-200">
              Contacts ({displayResult.people.length})
            </h2>
            {displayResult.people.map((person) => (
              <ContactCard
                key={person.name}
                person={person}
                emailResult={getEmailForPerson(person.name)}
                draft={getDraftForPerson(person.name)}
                jobId={jobId}
              />
            ))}
          </div>
        )}

        {/* Empty state */}
        {status === 'completed' && displayResult.people.length === 0 && (
          <div className="text-center py-12 text-gray-500">
            No contacts found. Try a different company or role.
          </div>
        )}
      </div>
    </div>
  );
}
