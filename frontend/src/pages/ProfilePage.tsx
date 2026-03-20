import { useState, useEffect, useRef } from 'react';
import {
  getProfile, saveProfile, deleteProfile,
  uploadResume, listResumes, deleteResume, getResumeDownloadUrl,
} from '../services/api';
import type { Project, WorkExperience, ResumeDoc } from '../services/api';

const inputClass =
  'w-full rounded-lg border border-gray-200 bg-white px-4 py-3 text-gray-900 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-gray-900 focus:border-transparent transition-shadow';

const smallInputClass =
  'w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-gray-900 focus:border-transparent transition-shadow';

function SectionDivider({ label }: { label: string }) {
  return (
    <div className="pt-4 pb-1">
      <h3 className="text-base font-semibold text-gray-900">{label}</h3>
      <div className="mt-1 h-px bg-gray-100" />
    </div>
  );
}

export default function ProfilePage() {
  const [name, setName] = useState('');
  const [linkedinUrl, setLinkedinUrl] = useState('');
  const [resumeUrl, setResumeUrl] = useState('');
  const [portfolioUrl, setPortfolioUrl] = useState('');
  const [universities, setUniversities] = useState('');
  const [previousCompanies, setPreviousCompanies] = useState('');
  const [skills, setSkills] = useState('');
  const [linkedinHeadline, setLinkedinHeadline] = useState('');
  const [linkedinSummary, setLinkedinSummary] = useState('');
  const [bio, setBio] = useState('');
  const [projects, setProjects] = useState<Project[]>([]);
  const [workExperience, setWorkExperience] = useState<WorkExperience[]>([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [activeResumeId, setActiveResumeId] = useState('');
  const [resumes, setResumes] = useState<ResumeDoc[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const resumeInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    Promise.all([getProfile(), listResumes()]).then(([profile, resumeList]) => {
      if (profile) {
        setName(profile.name || '');
        setLinkedinUrl(profile.linkedin_url || '');
        setResumeUrl(profile.resume_url || '');
        setPortfolioUrl(profile.portfolio_url || '');
        setUniversities((profile.universities || []).join(', '));
        setPreviousCompanies((profile.previous_companies || []).join(', '));
        setSkills((profile.skills || []).join(', '));
        setLinkedinHeadline(profile.linkedin_headline || '');
        setLinkedinSummary(profile.linkedin_summary || '');
        setBio(profile.bio || '');
        setActiveResumeId(profile.active_resume_id || '');
        setProjects(profile.projects || []);
        setWorkExperience(profile.work_experience || []);
      }
      setResumes(resumeList);
      setLoaded(true);
    });
  }, []);

  function splitCommas(s: string): string[] {
    return s
      .split(',')
      .map((x) => x.trim())
      .filter(Boolean);
  }

  // ── Project helpers ──────────────────────────────────────────────────
  function addProject() {
    setProjects((prev) => [...prev, { name: '', description: '', location: '' }]);
  }
  function removeProject(i: number) {
    setProjects((prev) => prev.filter((_, idx) => idx !== i));
  }
  function updateProject(i: number, field: keyof Project, value: string) {
    setProjects((prev) =>
      prev.map((p, idx) => (idx === i ? { ...p, [field]: value } : p)),
    );
  }

  // ── Work experience helpers ──────────────────────────────────────────
  function addWork() {
    setWorkExperience((prev) => [
      ...prev,
      { company: '', title: '', description: '', start_date: '', end_date: '' },
    ]);
  }
  function removeWork(i: number) {
    setWorkExperience((prev) => prev.filter((_, idx) => idx !== i));
  }
  function updateWork(i: number, field: keyof WorkExperience, value: string) {
    setWorkExperience((prev) =>
      prev.map((w, idx) => (idx === i ? { ...w, [field]: value } : w)),
    );
  }

  async function handleResumeUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setUploadError('');
    try {
      const doc = await uploadResume(file);
      setResumes((prev) => [doc, ...prev]);
      // Auto-select if it's the first resume
      if (!activeResumeId) setActiveResumeId(doc.resume_id);
    } catch (err: unknown) {
      setUploadError(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      setUploading(false);
      if (resumeInputRef.current) resumeInputRef.current.value = '';
    }
  }

  async function handleDeleteResume(resumeId: string) {
    await deleteResume(resumeId);
    setResumes((prev) => prev.filter((r) => r.resume_id !== resumeId));
    if (activeResumeId === resumeId) setActiveResumeId('');
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
        portfolio_url: portfolioUrl.trim(),
        universities: splitCommas(universities),
        previous_companies: splitCommas(previousCompanies),
        skills: splitCommas(skills),
        linkedin_headline: linkedinHeadline.trim(),
        linkedin_summary: linkedinSummary.trim(),
        bio: bio.trim(),
        active_resume_id: activeResumeId,
        projects: projects.filter((p) => p.name.trim()),
        work_experience: workExperience.filter((w) => w.company.trim() || w.title.trim()),
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
    setPortfolioUrl('');
    setUniversities('');
    setPreviousCompanies('');
    setSkills('');
    setLinkedinHeadline('');
    setLinkedinSummary('');
    setBio('');
    setProjects([]);
    setWorkExperience([]);
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
          connection matching and email personalization.
        </p>
      </div>

      {/* ── Resumes ── */}
      <div className="mb-6 rounded-xl border border-gray-200 bg-gray-50 p-4">
        <p className="text-sm font-medium text-gray-700 mb-1">Resumes</p>
        <p className="text-xs text-gray-500 mb-3">
          Upload PDFs — select one to use as context when writing cold emails.
          Hit "Save profile" below to apply the selection.
        </p>

        <input
          ref={resumeInputRef}
          type="file"
          accept=".pdf"
          className="hidden"
          onChange={handleResumeUpload}
        />

        {/* Resume list */}
        {resumes.length > 0 && (
          <div className="mb-3 space-y-2">
            {resumes.map((r) => (
              <div
                key={r.resume_id}
                onClick={() => setActiveResumeId(r.resume_id)}
                className={`flex items-center gap-3 rounded-lg border px-3 py-2.5 cursor-pointer transition-colors ${
                  activeResumeId === r.resume_id
                    ? 'border-gray-900 bg-white'
                    : 'border-gray-200 bg-white hover:border-gray-300'
                }`}
              >
                {/* Radio dot */}
                <div className={`w-4 h-4 rounded-full border-2 flex-shrink-0 flex items-center justify-center ${
                  activeResumeId === r.resume_id ? 'border-gray-900' : 'border-gray-300'
                }`}>
                  {activeResumeId === r.resume_id && (
                    <div className="w-2 h-2 rounded-full bg-gray-900" />
                  )}
                </div>

                {/* Filename + size */}
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-800 truncate">{r.filename}</p>
                  <p className="text-xs text-gray-400">{(r.size / 1024).toFixed(0)} KB</p>
                </div>

                {/* Download */}
                <a
                  href={getResumeDownloadUrl(r.resume_id)}
                  target="_blank"
                  rel="noreferrer"
                  onClick={(e) => e.stopPropagation()}
                  className="text-gray-400 hover:text-gray-700 transition-colors p-1"
                  title="Download"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                  </svg>
                </a>

                {/* Delete */}
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); handleDeleteResume(r.resume_id); }}
                  className="text-gray-400 hover:text-red-500 transition-colors p-1 cursor-pointer"
                  title="Remove"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="flex items-center gap-3">
          <button
            type="button"
            disabled={uploading}
            onClick={() => resumeInputRef.current?.click()}
            className="rounded-lg border border-gray-300 bg-white hover:bg-gray-50 disabled:opacity-50 px-3 py-2 text-sm font-medium text-gray-700 transition-colors cursor-pointer disabled:cursor-not-allowed"
          >
            {uploading ? 'Uploading...' : resumes.length === 0 ? 'Upload PDF' : '+ Upload another'}
          </button>
          {uploadError && <span className="text-xs text-red-600">{uploadError}</span>}
        </div>
      </div>

      <form onSubmit={handleSave} className="space-y-5">

        {/* ── Basic Info ── */}
        <SectionDivider label="Basic info" />

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">Name</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Ritesh Thipparthi"
            className={inputClass}
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">LinkedIn URL</label>
          <input
            type="url"
            value={linkedinUrl}
            onChange={(e) => setLinkedinUrl(e.target.value)}
            placeholder="https://linkedin.com/in/yourprofile"
            className={inputClass}
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">Resume URL</label>
          <input
            type="url"
            value={resumeUrl}
            onChange={(e) => setResumeUrl(e.target.value)}
            placeholder="https://drive.google.com/file/d/..."
            className={inputClass}
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">Portfolio / website URL</label>
          <input
            type="url"
            value={portfolioUrl}
            onChange={(e) => setPortfolioUrl(e.target.value)}
            placeholder="https://yourportfolio.com"
            className={inputClass}
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            Universities
            <span className="text-gray-400 font-normal ml-1">(comma-separated)</span>
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
            Skills
            <span className="text-gray-400 font-normal ml-1">(comma-separated)</span>
          </label>
          <input
            type="text"
            value={skills}
            onChange={(e) => setSkills(e.target.value)}
            placeholder="e.g. Python, React, Machine Learning"
            className={inputClass}
          />
        </div>

        {/* ── Bio ── */}
        <SectionDivider label="Bio for cold emails" />

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            Bio
            <span className="text-gray-400 font-normal ml-1">
              (used in every email — 2–3 sentences about you)
            </span>
          </label>
          <textarea
            value={bio}
            onChange={(e) => setBio(e.target.value)}
            rows={4}
            placeholder="e.g. CS junior at UMD with a focus on distributed systems. Built an LLM-powered RAG pipeline for 10K+ documents at Scale AI internship. Strong in Python, Go, and React."
            className={`${inputClass} resize-y`}
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            LinkedIn headline
            <span className="text-gray-400 font-normal ml-1">(paste from your profile)</span>
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
            <span className="text-gray-400 font-normal ml-1">(paste from your profile)</span>
          </label>
          <textarea
            value={linkedinSummary}
            onChange={(e) => setLinkedinSummary(e.target.value)}
            rows={5}
            placeholder="Paste your LinkedIn About section here..."
            className={`${inputClass} resize-y`}
          />
        </div>

        {/* ── Projects ── */}
        <SectionDivider label="Projects" />
        <p className="text-xs text-gray-500 -mt-2">
          Add projects to make your cold emails more specific. Include hackathon location, research lab, etc.
        </p>

        <div className="space-y-4">
          {projects.map((proj, i) => (
            <div key={i} className="rounded-xl border border-gray-200 p-4 space-y-3 relative">
              <button
                type="button"
                onClick={() => removeProject(i)}
                className="absolute top-3 right-3 text-gray-400 hover:text-gray-600 text-lg leading-none cursor-pointer"
                title="Remove project"
              >
                ×
              </button>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Project name</label>
                <input
                  type="text"
                  value={proj.name}
                  onChange={(e) => updateProject(i, 'name', e.target.value)}
                  placeholder="e.g. LLM-powered code reviewer"
                  className={smallInputClass}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">
                  Location / context
                  <span className="text-gray-400 font-normal ml-1">(optional)</span>
                </label>
                <input
                  type="text"
                  value={proj.location}
                  onChange={(e) => updateProject(i, 'location', e.target.value)}
                  placeholder="e.g. HackMIT 2024, MIT CSAIL, Personal project"
                  className={smallInputClass}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Description</label>
                <textarea
                  value={proj.description}
                  onChange={(e) => updateProject(i, 'description', e.target.value)}
                  rows={2}
                  placeholder="What it does, tech used, impact..."
                  className={`${smallInputClass} resize-y`}
                />
              </div>
            </div>
          ))}
          <button
            type="button"
            onClick={addProject}
            className="w-full rounded-lg border border-dashed border-gray-300 hover:border-gray-400 px-4 py-3 text-sm text-gray-500 hover:text-gray-700 transition-colors cursor-pointer"
          >
            + Add project
          </button>
        </div>

        {/* ── Work Experience ── */}
        <SectionDivider label="Work experience" />
        <p className="text-xs text-gray-500 -mt-2">
          Internships, full-time roles, research positions — anything worth mentioning in a cold email.
        </p>

        <div className="space-y-4">
          {workExperience.map((work, i) => (
            <div key={i} className="rounded-xl border border-gray-200 p-4 space-y-3 relative">
              <button
                type="button"
                onClick={() => removeWork(i)}
                className="absolute top-3 right-3 text-gray-400 hover:text-gray-600 text-lg leading-none cursor-pointer"
                title="Remove experience"
              >
                ×
              </button>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Company</label>
                  <input
                    type="text"
                    value={work.company}
                    onChange={(e) => updateWork(i, 'company', e.target.value)}
                    placeholder="e.g. Google"
                    className={smallInputClass}
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Title</label>
                  <input
                    type="text"
                    value={work.title}
                    onChange={(e) => updateWork(i, 'title', e.target.value)}
                    placeholder="e.g. SWE Intern"
                    className={smallInputClass}
                  />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">
                    Start
                    <span className="text-gray-400 font-normal ml-1">(optional)</span>
                  </label>
                  <input
                    type="text"
                    value={work.start_date}
                    onChange={(e) => updateWork(i, 'start_date', e.target.value)}
                    placeholder="e.g. Jun 2024"
                    className={smallInputClass}
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">
                    End
                    <span className="text-gray-400 font-normal ml-1">(blank = present)</span>
                  </label>
                  <input
                    type="text"
                    value={work.end_date}
                    onChange={(e) => updateWork(i, 'end_date', e.target.value)}
                    placeholder="e.g. Aug 2024"
                    className={smallInputClass}
                  />
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Description</label>
                <textarea
                  value={work.description}
                  onChange={(e) => updateWork(i, 'description', e.target.value)}
                  rows={2}
                  placeholder="What you built, impact, technologies..."
                  className={`${smallInputClass} resize-y`}
                />
              </div>
            </div>
          ))}
          <button
            type="button"
            onClick={addWork}
            className="w-full rounded-lg border border-dashed border-gray-300 hover:border-gray-400 px-4 py-3 text-sm text-gray-500 hover:text-gray-700 transition-colors cursor-pointer"
          >
            + Add work experience
          </button>
        </div>

        {/* ── Previous companies (warm path) ── */}
        <SectionDivider label="Previous companies (warm path)" />
        <p className="text-xs text-gray-500 -mt-2">
          Used to detect shared alumni connections — separate from work experience above.
        </p>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            Previous companies
            <span className="text-gray-400 font-normal ml-1">(comma-separated)</span>
          </label>
          <input
            type="text"
            value={previousCompanies}
            onChange={(e) => setPreviousCompanies(e.target.value)}
            placeholder="e.g. Google, Scale AI"
            className={inputClass}
          />
        </div>

        {/* ── Save / Clear ── */}
        <div className="pt-2 flex items-center gap-3">
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
