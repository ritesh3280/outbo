interface Props {
  children: React.ReactNode;
  /** Called when user clicks "New campaign" in sidebar or back in header */
  onGoHome?: () => void;
  /** Called when user clicks "Campaigns" in sidebar */
  onOpenCampaigns?: () => void;
  /** Which nav item to highlight: search, campaigns, or results (viewing a job) */
  activeNav?: 'search' | 'campaigns' | 'results';
  /** Show back link in main header (e.g. when viewing a campaign) */
  showBack?: boolean;
  backLabel?: string;
  /** Optional title in main header */
  title?: string;
}

const navButtonClass = (active: boolean) =>
  `block w-full text-left px-3 py-2 text-sm font-medium rounded-lg transition-colors cursor-pointer ${
    active
      ? 'bg-gray-100 text-gray-900'
      : 'text-gray-700 hover:bg-gray-100 hover:text-gray-900'
  }`;

export default function DashboardLayout({
  children,
  onGoHome,
  onOpenCampaigns,
  activeNav,
  showBack,
  backLabel = 'New campaign',
  title,
}: Props) {
  return (
    <div className="min-h-screen bg-white flex">
      {/* Sidebar */}
      <aside className="w-56 shrink-0 border-r border-gray-200 bg-white flex flex-col">
        <div className="p-5 border-b border-gray-100">
          <span className="text-lg font-semibold text-gray-900 tracking-tight">
            OutreachBot
          </span>
        </div>
        <nav className="p-3 space-y-0.5">
          <button
            type="button"
            onClick={() => onGoHome?.()}
            className={navButtonClass(activeNav === 'search')}
          >
            New campaign
          </button>
          <button
            type="button"
            onClick={() => onOpenCampaigns?.()}
            className={navButtonClass(activeNav === 'campaigns')}
          >
            Campaigns
          </button>
        </nav>
      </aside>

      {/* Main content */}
      <main className="flex-1 min-w-0 bg-white">
        {(showBack || title) && (
          <header className="border-b border-gray-100 px-8 py-4 bg-white">
            <div className="flex items-center gap-4">
              {showBack && onGoHome && (
                <button
                  type="button"
                  onClick={onGoHome}
                  className="text-sm text-gray-500 hover:text-gray-900 transition-colors cursor-pointer"
                >
                  ← {backLabel}
                </button>
              )}
              {title && (
                <h1 className="text-xl font-semibold text-gray-900 truncate">
                  {title}
                </h1>
              )}
            </div>
          </header>
        )}
        <div className="p-8">{children}</div>
      </main>
    </div>
  );
}
