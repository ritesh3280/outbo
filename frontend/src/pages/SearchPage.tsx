import { useState } from 'react';
import { startSearch } from '../services/api';

interface Props {
  onSearchStarted: (jobId: string) => void;
}

const inputClass =
  'w-full rounded-lg border border-gray-200 bg-white px-4 py-3 text-gray-900 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-gray-900 focus:border-transparent transition-shadow';

export default function SearchPage({ onSearchStarted }: Props) {
  const [company, setCompany] = useState('');
  const [role, setRole] = useState('');
  const [jobUrl, setJobUrl] = useState('');
  const [linkedinUrl, setLinkedinUrl] = useState('');
  const [companyWebsite, setCompanyWebsite] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!company.trim() || !role.trim()) return;

    setLoading(true);
    setError('');

    try {
      const { job_id } = await startSearch({
        company: company.trim(),
        role: role.trim(),
        job_url: jobUrl.trim() || undefined,
        linkedin_url: linkedinUrl.trim() || undefined,
        company_website: companyWebsite.trim() || undefined,
      });
      onSearchStarted(job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="max-w-xl">
      <div className="mb-8">
        <h2 className="text-2xl font-semibold text-gray-900 tracking-tight">
          New campaign
        </h2>
        <p className="mt-1 text-sm text-gray-500">
          Find contacts, discover emails, generate personalized outreach.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-5">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            Company *
          </label>
          <input
            type="text"
            value={company}
            onChange={(e) => setCompany(e.target.value)}
            placeholder="e.g. Stripe"
            className={inputClass}
            required
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            Company website
            <span className="text-gray-400 font-normal ml-1">(optional)</span>
          </label>
          <input
            type="url"
            value={companyWebsite}
            onChange={(e) => setCompanyWebsite(e.target.value)}
            placeholder="https://meetdandy.com"
            className={inputClass}
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            Role *
          </label>
          <input
            type="text"
            value={role}
            onChange={(e) => setRole(e.target.value)}
            placeholder="e.g. Software Engineering Intern"
            className={inputClass}
            required
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            Job posting URL
            <span className="text-gray-400 font-normal ml-1">(optional)</span>
          </label>
          <input
            type="url"
            value={jobUrl}
            onChange={(e) => setJobUrl(e.target.value)}
            placeholder="https://careers.example.com/jobs/swe-intern"
            className={inputClass}
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            Your LinkedIn URL
            <span className="text-gray-400 font-normal ml-1">(optional)</span>
          </label>
          <input
            type="url"
            value={linkedinUrl}
            onChange={(e) => setLinkedinUrl(e.target.value)}
            placeholder="https://linkedin.com/in/yourprofile"
            className={inputClass}
          />
        </div>

        {error && (
          <p className="text-sm text-red-600">{error}</p>
        )}

        <button
          type="submit"
          disabled={loading || !company.trim() || !role.trim()}
          className="w-full rounded-lg bg-gray-900 hover:bg-gray-800 disabled:bg-gray-200 disabled:text-gray-400 text-white px-4 py-3 text-sm font-medium transition-colors cursor-pointer disabled:cursor-not-allowed"
        >
          {loading ? 'Starting…' : 'Find contacts'}
        </button>
      </form>
    </div>
  );
}
