import { useState, useEffect } from 'react';
import type { Person, EmailResult, EmailDraft } from '../services/api';
import { editEmail, generateEmail } from '../services/api';

interface Props {
  person: Person;
  emailResult?: EmailResult;
  draft?: EmailDraft;
  jobId: string;
  onDraftUpdated?: (name: string, draft: Partial<EmailDraft>) => void;
  onEmailGenerated?: (draft: EmailDraft) => void;
}

const CONFIDENCE_STYLES: Record<string, string> = {
  high: 'bg-green-50 text-green-700 border-green-200',
  medium: 'bg-amber-50 text-amber-700 border-amber-200',
  low: 'bg-gray-100 text-gray-600 border-gray-200',
};

const inputClass =
  'w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-gray-900 focus:border-transparent';

export default function ContactCard({
  person,
  emailResult,
  draft,
  jobId,
  onDraftUpdated,
  onEmailGenerated,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const [editSubject, setEditSubject] = useState(draft?.subject || '');
  const [editBody, setEditBody] = useState(draft?.body || '');
  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);
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
      // ignore
    } finally {
      setSaving(false);
    }
  }

  async function handleGenerateEmail() {
    if (!emailResult?.email) return;
    setGenerating(true);
    try {
      const newDraft = await generateEmail({ job_id: jobId, name: person.name });
      onEmailGenerated?.(newDraft);
      setEditSubject(newDraft.subject);
      setEditBody(newDraft.body);
      setExpanded(true);
    } catch {
      // ignore
    } finally {
      setGenerating(false);
    }
  }

  function handleCopy() {
    const text = `Subject: ${editSubject}\n\n${editBody}`;
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  useEffect(() => {
    if (draft) {
      setEditSubject(draft.subject);
      setEditBody(draft.body);
    }
  }, [draft?.subject, draft?.body]);

  return (
    <div className="rounded-lg border border-gray-200 bg-white overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="w-full px-4 py-3 flex items-center justify-between hover:bg-gray-50 transition-colors text-left cursor-pointer"
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="font-medium text-gray-900 truncate">{person.name}</h3>
            <span className="text-xs text-gray-400">
              {(person.priority_score * 100).toFixed(0)}
            </span>
          </div>
          <p className="text-sm text-gray-500 truncate">{person.title}</p>
          {emailResult?.email && (
            <div className="flex items-center gap-2 mt-1">
              <span className="text-sm text-gray-600">{emailResult.email}</span>
              <span
                className={`text-xs px-1.5 py-0.5 rounded border ${CONFIDENCE_STYLES[confidence]}`}
              >
                {confidence}
              </span>
            </div>
          )}
        </div>
        <div className="flex items-center gap-2 ml-4 shrink-0">
          {emailResult?.email && !draft && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                handleGenerateEmail();
              }}
              disabled={generating}
              className="rounded-lg bg-gray-900 hover:bg-gray-800 disabled:bg-gray-100 disabled:text-gray-400 text-white px-3 py-1.5 text-sm font-medium transition-colors cursor-pointer disabled:cursor-not-allowed"
            >
              {generating ? 'Generating…' : 'Generate email'}
            </button>
          )}
          {person.linkedin_url && (
            <a
              href={person.linkedin_url}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="text-sm text-gray-600 hover:text-gray-900"
            >
              LinkedIn
            </a>
          )}
          <span className="text-gray-400 text-sm">
            {expanded ? '▲' : '▼'}
          </span>
        </div>
      </button>

      {expanded && (draft || (editSubject && editBody)) && (
        <div className="px-4 pb-4 pt-3 space-y-3 border-t border-gray-100">
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">
              Subject
            </label>
            <input
              type="text"
              value={editSubject}
              onChange={(e) => setEditSubject(e.target.value)}
              className={inputClass}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">
              Body
            </label>
            <textarea
              value={editBody}
              onChange={(e) => setEditBody(e.target.value)}
              rows={8}
              className={`${inputClass} resize-y`}
            />
          </div>
          {draft?.personalization_notes && (
            <p className="text-xs text-gray-500">
              Personalization: {draft.personalization_notes}
            </p>
          )}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="rounded-lg bg-gray-900 hover:bg-gray-800 disabled:bg-gray-200 text-white px-4 py-2 text-sm font-medium transition-colors cursor-pointer disabled:cursor-not-allowed"
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button
              type="button"
              onClick={handleCopy}
              className="rounded-lg border border-gray-200 hover:bg-gray-50 px-4 py-2 text-sm font-medium text-gray-700 transition-colors cursor-pointer"
            >
              {copied ? 'Copied!' : 'Copy'}
            </button>
          </div>
        </div>
      )}

      {expanded && !draft && !editSubject && !editBody && (
        <div className="px-4 pb-4 pt-3 border-t border-gray-100">
          <p className="text-sm text-gray-500">
            {emailResult?.email
              ? 'Click “Generate email” above to create a personalized draft.'
              : 'No email address found for this contact.'}
          </p>
        </div>
      )}
    </div>
  );
}
