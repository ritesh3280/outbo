import { useState } from 'react';
import DashboardLayout from './components/DashboardLayout';
import SearchPage from './pages/SearchPage';
import ResultsPage from './pages/ResultsPage';
import HistoryPage from './pages/HistoryPage';
import ProfilePage from './pages/ProfilePage';

function App() {
  const [jobId, setJobId] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [showProfile, setShowProfile] = useState(false);

  const goHome = () => {
    setJobId(null);
    setShowHistory(false);
    setShowProfile(false);
  };

  const openCampaigns = () => {
    setJobId(null);
    setShowHistory(true);
    setShowProfile(false);
  };

  const openProfile = () => {
    setJobId(null);
    setShowHistory(false);
    setShowProfile(true);
  };

  if (jobId) {
    return (
      <DashboardLayout
        onGoHome={goHome}
        onOpenCampaigns={openCampaigns}
        onOpenProfile={openProfile}
        showBack
        backLabel="New campaign"
        activeNav="results"
      >
        <ResultsPage jobId={jobId} />
      </DashboardLayout>
    );
  }

  if (showProfile) {
    return (
      <DashboardLayout
        onGoHome={goHome}
        onOpenCampaigns={openCampaigns}
        onOpenProfile={openProfile}
        activeNav="profile"
      >
        <ProfilePage />
      </DashboardLayout>
    );
  }

  if (showHistory) {
    return (
      <DashboardLayout
        onGoHome={goHome}
        onOpenCampaigns={openCampaigns}
        onOpenProfile={openProfile}
        activeNav="campaigns"
      >
        <HistoryPage onOpenJob={(id) => { setJobId(id); setShowHistory(false); }} />
      </DashboardLayout>
    );
  }

  return (
    <DashboardLayout
      onGoHome={goHome}
      onOpenCampaigns={openCampaigns}
      onOpenProfile={openProfile}
      activeNav="search"
    >
      <SearchPage onSearchStarted={setJobId} />
    </DashboardLayout>
  );
}

export default App;
