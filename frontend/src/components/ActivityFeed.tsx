import type { ActivityLogEntry } from '../services/api';

const TYPE_ICONS: Record<string, string> = {
  status: '\u{1F50D}',
  person_found: '\u{1F464}',
  email_found: '\u{1F4E7}',
  email_drafted: '\u{270D}\u{FE0F}',
  complete: '\u{2705}',
  error: '\u{26A0}\u{FE0F}',
};

interface Props {
  entries: ActivityLogEntry[];
  status: string;
}

export default function ActivityFeed({ entries, status }: Props) {
  const isRunning = !['completed', 'failed'].includes(status);

  return (
    <div className="rounded-xl bg-gray-900 border border-gray-800 p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-gray-300">Agent Activity</h3>
        {isRunning && (
          <span className="flex items-center gap-1.5 text-xs text-blue-400">
            <span className="h-1.5 w-1.5 rounded-full bg-blue-400 animate-pulse" />
            Running
          </span>
        )}
      </div>

      <div className="space-y-2 max-h-48 overflow-y-auto">
        {entries.map((entry, i) => (
          <div key={i} className="flex items-start gap-2 text-sm">
            <span className="shrink-0 mt-0.5">
              {TYPE_ICONS[entry.type] || TYPE_ICONS.status}
            </span>
            <span className={entry.type === 'error' ? 'text-red-400' : 'text-gray-400'}>
              {entry.message}
            </span>
          </div>
        ))}
        {entries.length === 0 && (
          <p className="text-sm text-gray-600">Waiting for agent to start...</p>
        )}
      </div>
    </div>
  );
}
