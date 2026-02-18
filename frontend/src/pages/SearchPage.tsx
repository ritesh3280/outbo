import { useState } from 'react';
import { startSearch } from '../services/api';

interface Props {
  onSearchStarted: (jobId: string) => void;
}

export default function SearchPage({ onSearchStarted }: Props) {
  const [company, setCompany] = useState('');
  const [role, setRole] = useState('');
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
    <div className="min-h-screen bg-gray-950 text-white flex items-center justify-center px-4">
      <div className="w-full max-w-lg space-y-8">
        <div className="text-center space-y-2">
          <h1 className="text-4xl font-bold tracking-tight">OutreachBot</h1>
          <p className="text-gray-400">
            Find contacts, discover emails, generate personalized outreach
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1">
              Company *
            </label>
            <input
              type="text"
              value={company}
              onChange={(e) => setCompany(e.target.value)}
              placeholder="e.g. Stripe"
              className="w-full rounded-lg bg-gray-900 border border-gray-700 px-4 py-3 text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              required
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1">
              Company Website
              <span className="text-gray-500 ml-1">(optional — helps find the right email domain)</span>
            </label>
            <input
              type="url"
              value={companyWebsite}
              onChange={(e) => setCompanyWebsite(e.target.value)}
              placeholder="https://meetdandy.com"
              className="w-full rounded-lg bg-gray-900 border border-gray-700 px-4 py-3 text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1">
              Role *
            </label>
            <input
              type="text"
              value={role}
              onChange={(e) => setRole(e.target.value)}
              placeholder="e.g. Software Engineering Intern"
              className="w-full rounded-lg bg-gray-900 border border-gray-700 px-4 py-3 text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              required
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1">
              Your LinkedIn URL
              <span className="text-gray-500 ml-1">(optional)</span>
            </label>
            <input
              type="url"
              value={linkedinUrl}
              onChange={(e) => setLinkedinUrl(e.target.value)}
              placeholder="https://linkedin.com/in/yourprofile"
              className="w-full rounded-lg bg-gray-900 border border-gray-700 px-4 py-3 text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>

          {error && (
            <p className="text-red-400 text-sm">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading || !company.trim() || !role.trim()}
            className="w-full rounded-lg bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 px-4 py-3 font-medium transition-colors cursor-pointer disabled:cursor-not-allowed"
          >
            {loading ? 'Starting...' : 'Find Contacts'}
          </button>
        </form>
      </div>
    </div>
  );
}
