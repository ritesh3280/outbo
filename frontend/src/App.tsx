import { useState } from 'react';
import DashboardLayout from './components/DashboardLayout';
import SearchPage from './pages/SearchPage';
import ResultsPage from './pages/ResultsPage';
import HistoryPage from './pages/HistoryPage';

function App() {
  const [jobId, setJobId] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  const goHome = () => {
    setJobId(null);
    setShowHistory(false);
  };

  const openCampaigns = () => {
    setJobId(null);
    setShowHistory(true);
  };

  if (jobId) {
    return (
      <DashboardLayout
        onGoHome={goHome}
        onOpenCampaigns={openCampaigns}
        showBack
        backLabel="New campaign"
        activeNav="results"
      >
        <ResultsPage jobId={jobId} />
      </DashboardLayout>
    );
  }

  if (showHistory) {
    return (
      <DashboardLayout
        onGoHome={goHome}
        onOpenCampaigns={openCampaigns}
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
      activeNav="search"
    >
      <SearchPage onSearchStarted={setJobId} />
    </DashboardLayout>
  );
}

export default App;
