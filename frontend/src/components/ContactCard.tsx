import { useState } from 'react';
import type { Person, EmailResult, EmailDraft } from '../services/api';
import { editEmail } from '../services/api';

interface Props {
  person: Person;
  emailResult?: EmailResult;
  draft?: EmailDraft;
  jobId: string;
  onDraftUpdated?: (name: string, draft: Partial<EmailDraft>) => void;
}

const CONFIDENCE_STYLES = {
  high: 'bg-green-900/50 text-green-300 border-green-700',
  medium: 'bg-yellow-900/50 text-yellow-300 border-yellow-700',
  low: 'bg-gray-800 text-gray-400 border-gray-700',
};

export default function ContactCard({ person, emailResult, draft, jobId, onDraftUpdated }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [editSubject, setEditSubject] = useState(draft?.subject || '');
  const [editBody, setEditBody] = useState(draft?.body || '');
  const [saving, setSaving] = useState(false);
  const [copied, setCopied] = useState(false);

  const confidence = emailResult?.confidence || 'low';

  async function handleSave() {
    setSaving(true);
    try {
      await editEmail({
        job_id: jobId,
        name: person.name,
        subject: editSubject,
        body: editBody,
      });
      onDraftUpdated?.(person.name, { subject: editSubject, body: editBody });
    } catch {
      // silently fail for now
    } finally {
      setSaving(false);
    }
  }

  function handleCopy() {
    const text = `Subject: ${editSubject}\n\n${editBody}`;
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  // Sync local state when draft prop changes
  if (draft && editSubject === '' && editBody === '') {
    setEditSubject(draft.subject);
    setEditBody(draft.body);
  }

  return (
    <div className="rounded-xl bg-gray-900 border border-gray-800 overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full px-5 py-4 flex items-center justify-between hover:bg-gray-800/50 transition-colors text-left cursor-pointer"
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3">
            <h3 className="font-medium text-white truncate">{person.name}</h3>
            <span className="text-xs text-gray-500">{person.priority_score.toFixed(2)}</span>
          </div>
          <p className="text-sm text-gray-400 truncate">{person.title}</p>
          {emailResult?.email && (
            <div className="flex items-center gap-2 mt-1">
              <span className="text-sm text-gray-300">{emailResult.email}</span>
              <span className={`text-xs px-1.5 py-0.5 rounded border ${CONFIDENCE_STYLES[confidence]}`}>
                {confidence}
              </span>
            </div>
          )}
        </div>
        <div className="flex items-center gap-3 ml-4 shrink-0">
          {person.linkedin_url && (
            <a
              href={person.linkedin_url}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="text-blue-400 hover:text-blue-300 text-sm"
            >
              LinkedIn
            </a>
          )}
          <span className="text-gray-500 text-sm">{expanded ? '\u25B2' : '\u25BC'}</span>
        </div>
      </button>

      {/* Expanded: Email draft */}
      {expanded && draft && (
        <div className="px-5 pb-5 space-y-3 border-t border-gray-800 pt-4">
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">Subject</label>
            <input
              type="text"
              value={editSubject}
              onChange={(e) => setEditSubject(e.target.value)}
              className="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">Body</label>
            <textarea
              value={editBody}
              onChange={(e) => setEditBody(e.target.value)}
              rows={8}
              className="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-white focus:outline-none focus:ring-1 focus:ring-blue-500 resize-y"
            />
          </div>
          {draft.personalization_notes && (
            <p className="text-xs text-gray-500">
              Personalization: {draft.personalization_notes}
            </p>
          )}
          <div className="flex gap-2">
            <button
              onClick={handleSave}
              disabled={saving}
              className="rounded-lg bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 px-4 py-2 text-sm font-medium transition-colors cursor-pointer"
            >
              {saving ? 'Saving...' : 'Save'}
            </button>
            <button
              onClick={handleCopy}
              className="rounded-lg bg-gray-800 hover:bg-gray-700 border border-gray-700 px-4 py-2 text-sm font-medium transition-colors cursor-pointer"
            >
              {copied ? 'Copied!' : 'Copy'}
            </button>
          </div>
        </div>
      )}

      {/* Expanded: No draft yet */}
      {expanded && !draft && (
        <div className="px-5 pb-5 border-t border-gray-800 pt-4">
          <p className="text-sm text-gray-500">Email draft not yet generated...</p>
        </div>
      )}
    </div>
  );
}
