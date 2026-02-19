import { useEffect, useState } from 'react';
import { getHistory } from '../services/api';
import type { HistoryEntry } from '../services/api';

const STATUS_STYLES: Record<string, string> = {
  completed: 'border-green-200 text-green-700 bg-green-50',
  failed: 'border-red-200 text-red-700 bg-red-50',
  pending: 'border-gray-200 text-gray-600 bg-gray-50',
  finding_people: 'border-blue-200 text-blue-700 bg-blue-50',
  finding_emails: 'border-blue-200 text-blue-700 bg-blue-50',
  researching: 'border-blue-200 text-blue-700 bg-blue-50',
  generating_emails: 'border-blue-200 text-blue-700 bg-blue-50',
};

interface Props {
  onOpenJob: (jobId: string) => void;
}

export default function HistoryPage({ onOpenJob }: Props) {
  const [entries, setEntries] = useState<HistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getHistory()
      .then((data) => {
        if (!cancelled) setEntries(data);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <div className="py-12 text-center text-sm text-gray-500">
        Loading campaigns…
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
        {error}
      </div>
    );
  }

  if (entries.length === 0) {
    return (
      <div className="max-w-xl">
        <h2 className="text-2xl font-semibold text-gray-900 tracking-tight">
          Campaigns
        </h2>
        <p className="mt-1 text-sm text-gray-500 mb-8">
          Past outreach campaigns (company + role). One company can have multiple roles.
        </p>
        <div className="rounded-lg border border-gray-200 bg-gray-50/50 px-6 py-12 text-center text-sm text-gray-500">
          No campaigns yet. Start with <strong>New campaign</strong>.
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-2xl">
      <h2 className="text-2xl font-semibold text-gray-900 tracking-tight">
        Campaigns
      </h2>
      <p className="mt-1 text-sm text-gray-500 mb-6">
        Past outreach campaigns. Same company can appear for different roles.
      </p>
      <ul className="space-y-2">
        {entries.map((entry) => (
          <li key={entry.job_id}>
            <button
              type="button"
              onClick={() => onOpenJob(entry.job_id)}
              className="w-full rounded-lg border border-gray-200 bg-white px-4 py-3 text-left hover:bg-gray-50 transition-colors cursor-pointer flex items-center justify-between gap-4"
            >
              <div className="min-w-0">
                <div className="font-medium text-gray-900 truncate">
                  {entry.company} — {entry.role}
                </div>
                <div className="text-xs text-gray-500 mt-0.5">
                  {entry.people_count} contacts · {entry.drafts_count} drafts
                </div>
              </div>
              <span
                className={`shrink-0 text-xs font-medium px-2 py-1 rounded-full border ${
                  STATUS_STYLES[entry.status] ?? 'border-gray-200 text-gray-600 bg-gray-50'
                }`}
              >
                {entry.status}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
