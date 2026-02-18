import { useState } from 'react';
import SearchPage from './pages/SearchPage';
import ResultsPage from './pages/ResultsPage';

function App() {
  const [jobId, setJobId] = useState<string | null>(null);

  if (jobId) {
    return <ResultsPage jobId={jobId} onBack={() => setJobId(null)} />;
  }

  return <SearchPage onSearchStarted={setJobId} />;
}

export default App;
