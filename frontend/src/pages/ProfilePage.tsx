import { useState, useEffect } from 'react';
import { getProfile, saveProfile, deleteProfile } from '../services/api';
import type { UserProfileDoc } from '../services/api';

const inputClass =
  'w-full rounded-lg border border-gray-200 bg-white px-4 py-3 text-gray-900 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-gray-900 focus:border-transparent transition-shadow';

export default function ProfilePage() {
  const [name, setName] = useState('');
  const [linkedinUrl, setLinkedinUrl] = useState('');
  const [resumeUrl, setResumeUrl] = useState('');
  const [universities, setUniversities] = useState('');
  const [previousCompanies, setPreviousCompanies] = useState('');
  const [skills, setSkills] = useState('');
  const [linkedinHeadline, setLinkedinHeadline] = useState('');
  const [linkedinSummary, setLinkedinSummary] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    getProfile().then((profile) => {
      if (profile) {
        setName(profile.name || '');
        setLinkedinUrl(profile.linkedin_url || '');
        setResumeUrl(profile.resume_url || '');
        setUniversities((profile.universities || []).join(', '));
        setPreviousCompanies((profile.previous_companies || []).join(', '));
        setSkills((profile.skills || []).join(', '));
        setLinkedinHeadline(profile.linkedin_headline || '');
        setLinkedinSummary(profile.linkedin_summary || '');
      }
      setLoaded(true);
    });
  }, []);

  function splitCommas(s: string): string[] {
    return s
      .split(',')
      .map((x) => x.trim())
      .filter(Boolean);
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setSaved(false);
    try {
      await saveProfile({
        name: name.trim(),
        linkedin_url: linkedinUrl.trim(),
        resume_url: resumeUrl.trim(),
        universities: splitCommas(universities),
        previous_companies: splitCommas(previousCompanies),
        skills: splitCommas(skills),
        linkedin_headline: linkedinHeadline.trim(),
        linkedin_summary: linkedinSummary.trim(),
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch {
      // ignore
    } finally {
      setSaving(false);
    }
  }

  async function handleClear() {
    if (!confirm('Clear your saved profile? This cannot be undone.')) return;
    await deleteProfile();
    setName('');
    setLinkedinUrl('');
    setResumeUrl('');
    setUniversities('');
    setPreviousCompanies('');
    setSkills('');
    setLinkedinHeadline('');
    setLinkedinSummary('');
  }

  if (!loaded) return null;

  return (
    <div className="max-w-xl">
      <div className="mb-8">
        <h2 className="text-2xl font-semibold text-gray-900 tracking-tight">
          Your profile
        </h2>
        <p className="mt-1 text-sm text-gray-500">
          Save your info once — it's reused across all campaigns for warm
          connection matching.
        </p>
      </div>

      <form onSubmit={handleSave} className="space-y-5">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            Name
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Ritesh Kumar"
            className={inputClass}
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            LinkedIn URL
          </label>
          <input
            type="url"
            value={linkedinUrl}
            onChange={(e) => setLinkedinUrl(e.target.value)}
            placeholder="https://linkedin.com/in/yourprofile"
            className={inputClass}
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            Resume URL
          </label>
          <input
            type="url"
            value={resumeUrl}
            onChange={(e) => setResumeUrl(e.target.value)}
            placeholder="https://drive.google.com/file/d/..."
            className={inputClass}
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            Universities
            <span className="text-gray-400 font-normal ml-1">
              (comma-separated)
            </span>
          </label>
          <input
            type="text"
            value={universities}
            onChange={(e) => setUniversities(e.target.value)}
            placeholder="e.g. University of Maryland, Stanford"
            className={inputClass}
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            Previous companies
            <span className="text-gray-400 font-normal ml-1">
              (comma-separated)
            </span>
          </label>
          <input
            type="text"
            value={previousCompanies}
            onChange={(e) => setPreviousCompanies(e.target.value)}
            placeholder="e.g. Google, Meta"
            className={inputClass}
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            Skills
            <span className="text-gray-400 font-normal ml-1">
              (comma-separated)
            </span>
          </label>
          <input
            type="text"
            value={skills}
            onChange={(e) => setSkills(e.target.value)}
            placeholder="e.g. Python, React, Machine Learning"
            className={inputClass}
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            LinkedIn headline
            <span className="text-gray-400 font-normal ml-1">
              (paste from your profile)
            </span>
          </label>
          <input
            type="text"
            value={linkedinHeadline}
            onChange={(e) => setLinkedinHeadline(e.target.value)}
            placeholder="e.g. CS Student at UMD | SWE Intern @ Google"
            className={inputClass}
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            LinkedIn summary / about
            <span className="text-gray-400 font-normal ml-1">
              (paste from your profile)
            </span>
          </label>
          <textarea
            value={linkedinSummary}
            onChange={(e) => setLinkedinSummary(e.target.value)}
            rows={5}
            placeholder="Paste your LinkedIn About section here..."
            className={`${inputClass} resize-y`}
          />
        </div>

        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={saving}
            className="rounded-lg bg-gray-900 hover:bg-gray-800 disabled:bg-gray-200 disabled:text-gray-400 text-white px-4 py-3 text-sm font-medium transition-colors cursor-pointer disabled:cursor-not-allowed"
          >
            {saving ? 'Saving...' : 'Save profile'}
          </button>
          <button
            type="button"
            onClick={handleClear}
            className="rounded-lg border border-gray-200 hover:bg-gray-50 px-4 py-3 text-sm font-medium text-gray-700 transition-colors cursor-pointer"
          >
            Clear profile
          </button>
          {saved && (
            <span className="text-sm text-green-600 font-medium">Saved!</span>
          )}
        </div>
      </form>
    </div>
  );
}
