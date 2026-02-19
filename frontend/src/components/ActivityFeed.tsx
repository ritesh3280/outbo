import type { ActivityLogEntry } from '../services/api';

const TYPE_ICONS: Record<string, string> = {
  status: '·',
  person_found: '👤',
  email_found: '✉',
  email_drafted: '✎',
  complete: '✓',
  error: '!',
};

interface Props {
  entries: ActivityLogEntry[];
  status: string;
}

export default function ActivityFeed({ entries, status }: Props) {
  const isRunning = !['completed', 'failed'].includes(status);

  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50/50 p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-gray-700">Activity</h3>
        {isRunning && (
          <span className="flex items-center gap-1.5 text-xs text-gray-500">
            <span className="h-1.5 w-1.5 rounded-full bg-gray-400 animate-pulse" />
            Running
          </span>
        )}
      </div>
      <div className="space-y-2 max-h-40 overflow-y-auto">
        {entries.map((entry, i) => (
          <div key={i} className="flex items-start gap-2 text-sm">
            <span className="shrink-0 mt-0.5 text-gray-400">
              {TYPE_ICONS[entry.type] || TYPE_ICONS.status}
            </span>
            <span
              className={
                entry.type === 'error' ? 'text-red-600' : 'text-gray-600'
              }
            >
              {entry.message}
            </span>
          </div>
        ))}
        {entries.length === 0 && (
          <p className="text-sm text-gray-400">Waiting for agent…</p>
        )}
      </div>
    </div>
  );
}
