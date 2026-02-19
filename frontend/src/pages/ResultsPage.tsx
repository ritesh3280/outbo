import { useEffect, useState, useCallback } from 'react';
import { getSearchResult, postMoreLeads } from '../services/api';
import type { SearchResult, EmailDraft } from '../services/api';
import { useWebSocket } from '../hooks/useWebSocket';
import ActivityFeed from '../components/ActivityFeed';
import ContactCard from '../components/ContactCard';

interface Props {
  jobId: string;
  onBack?: () => void;
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
  'completed',
];

export default function ResultsPage({ jobId }: Props) {
  const [result, setResult] = useState<SearchResult | null>(null);
  const [moreLeadsLoading, setMoreLeadsLoading] = useState(false);
  const [moreLeadsError, setMoreLeadsError] = useState<string | null>(null);
  const { data: wsData } = useWebSocket(jobId);

  const displayResult = wsData || result;

  const poll = useCallback(async () => {
    try {
      const data = await getSearchResult(jobId);
      setResult(data);
    } catch {
      // ignore
    }
  }, [jobId]);

  useEffect(() => {
    poll();
    const interval = setInterval(poll, 3000);
    return () => clearInterval(interval);
  }, [poll]);

  if (!displayResult) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="animate-pulse text-gray-400 text-sm">Loading…</div>
      </div>
    );
  }

  const status = displayResult.status;
  const isRunning = !['completed', 'failed'].includes(status);
  const stepIndex = STEP_ORDER.indexOf(status);
  const progress = Math.max(0, Math.min(100, (stepIndex / (STEP_ORDER.length - 1)) * 100));

  function getEmailForPerson(name: string) {
    return displayResult!.email_results.find((e) => e.name === name);
  }
  function getDraftForPerson(name: string) {
    return displayResult!.email_drafts.find((d) => d.name === name);
  }

  function handleEmailGenerated(newDraft: EmailDraft) {
    setResult((prev) =>
      prev
        ? { ...prev, email_drafts: [...prev.email_drafts, newDraft] }
        : null
    );
  }

  async function handleGenerateMoreLeads() {
    setMoreLeadsError(null);
    setMoreLeadsLoading(true);
    try {
      await postMoreLeads(jobId);
    } catch (e) {
      setMoreLeadsError(e instanceof Error ? e.message : 'Failed to start');
    } finally {
      setMoreLeadsLoading(false);
    }
  }

  return (
    <div className="max-w-3xl space-y-6">
      {/* Title + status + more leads */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <h2 className="text-xl font-semibold text-gray-900 truncate">
          {displayResult.company} — {displayResult.role}
        </h2>
        <div className="flex items-center gap-2 shrink-0">
          {status === 'completed' && (
            <button
              type="button"
              onClick={handleGenerateMoreLeads}
              disabled={moreLeadsLoading}
              className="text-sm font-medium text-gray-700 bg-white border border-gray-200 rounded-lg px-3 py-1.5 hover:bg-gray-50 disabled:opacity-50 disabled:pointer-events-none"
            >
              {moreLeadsLoading ? 'Finding more…' : 'Generate more leads'}
            </button>
          )}
          <span
            className={`text-xs font-medium px-2.5 py-1 rounded-full border ${
              status === 'completed'
                ? 'border-green-200 text-green-700 bg-green-50'
                : status === 'failed'
                  ? 'border-red-200 text-red-700 bg-red-50'
                  : 'border-gray-200 text-gray-700 bg-gray-50'
            }`}
          >
            {STATUS_LABELS[status] || status}
          </span>
        </div>
      </div>
      {moreLeadsError && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">
          {moreLeadsError}
        </div>
      )}

      {/* Progress */}
      {isRunning && (
        <div className="h-0.5 w-full bg-gray-100 rounded-full overflow-hidden">
          <div
            className="h-full bg-gray-900 rounded-full transition-all duration-500"
            style={{ width: `${progress}%` }}
          />
        </div>
      )}

      <ActivityFeed entries={displayResult.activity_log} status={status} />

      {displayResult.error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {displayResult.error}
        </div>
      )}

      {displayResult.people.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-sm font-medium text-gray-500">
            Contacts ({displayResult.people.length})
          </h3>
          <div className="space-y-2">
            {displayResult.people.map((person) => (
              <ContactCard
                key={person.linkedin_url || person.name}
                person={person}
                emailResult={getEmailForPerson(person.name)}
                draft={getDraftForPerson(person.name)}
                jobId={jobId}
                onEmailGenerated={handleEmailGenerated}
              />
            ))}
          </div>
        </div>
      )}

      {status === 'completed' && displayResult.people.length === 0 && (
        <div className="py-12 text-center text-sm text-gray-500">
          No contacts found. Try a different company or role.
        </div>
      )}
    </div>
  );
}
