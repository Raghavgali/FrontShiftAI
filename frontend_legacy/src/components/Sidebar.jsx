import React, { useState } from 'react';
import FrontShiftLogo from './FrontShiftLogo';

const Sidebar = ({
  activeView,
  setActiveView,
  width = 320,
  chatHistory = [],
  onNewChat,
  onLoadChat,
  onDeleteChat,
  currentChatId,
  userInfo,
  onLogout,
  isOpen = true,
  onToggle,
  isMobile = false
}) => {
  const [searchQuery, setSearchQuery] = useState('');

  // Filter chats based on search query
  const filteredChatHistory = chatHistory.map(group => ({
    ...group,
    chats: group.chats.filter(chat =>
      chat.title.toLowerCase().includes(searchQuery.toLowerCase())
    )
  })).filter(group => group.chats.length > 0);

  return (
    <>
      <div
        className={`fixed left-0 top-0 h-screen flex flex-col z-30 transition-all duration-300 ease-in-out sidebar-glass ${isOpen ? 'translate-x-0' : '-translate-x-full'
          }`}
        style={{
          width: `${width}px`,
          background: 'rgba(0, 0, 0, 0.2)',
          backdropFilter: 'blur(24px)',
          WebkitBackdropFilter: 'blur(24px)',
          borderRight: '1px solid rgba(255, 255, 255, 0.05)'
        }}
      >
        {/* Toggle Button Inside Sidebar (Close) */}
        <button
          onClick={onToggle}
          className="absolute -right-3 top-6 w-6 h-6 bg-[#1a1a24] text-white/60 hover:text-white border border-white/10 rounded-full flex items-center justify-center cursor-pointer z-50 hover:bg-white/10 transition-all shadow-lg"
          title="Close Sidebar"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </button>

        {/* Logo */}
        <div className="px-6 py-5 border-b border-white/5">
          <FrontShiftLogo size={32} showText={true} />
        </div>

        {/* User Info */}
        {userInfo && (
          <div className="px-4 py-3 border-b border-white/5 bg-white/5">
            <div className="flex items-center justify-between">
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-white/90 truncate">
                  {userInfo.name || 'User'}
                </p>
                <p className="text-xs text-white/50 truncate mt-0.5">
                  {userInfo.email}
                </p>
              </div>
              <button
                onClick={onLogout}
                className="ml-2 p-2 hover:bg-white/10 rounded-lg transition-all group"
                title="Logout"
              >
                <svg
                  className="w-4 h-4 text-white/60 group-hover:text-white transition-colors"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"
                  />
                </svg>
              </button>
            </div>
          </div>
        )}

        {/* Search Bar */}
        <div className="px-4 py-3 border-b border-white/5">
          <div className="flex items-center space-x-2">
            <div className="relative flex-1">
              <input
                type="text"
                placeholder="Search chats"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 pl-8 pr-3 text-sm text-white/90 placeholder-white/35 focus:outline-none focus:border-white/20 focus:bg-white/8 transition-all"
              />
              <svg
                className="absolute left-2.5 top-1/2 transform -translate-y-1/2 w-4 h-4 text-white/35 pointer-events-none"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </div>
            <button
              onClick={onNewChat}
              className="flex items-center justify-center w-9 h-9 bg-white/10 hover:bg-white/15 border border-white/10 rounded-lg text-white/80 hover:text-white transition-all cursor-pointer"
              title="New Chat"
            >
              <svg
                className="w-5 h-5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
            </button>
          </div>
        </div>

        {/* Recent Chats */}
        <div className="px-4 py-4 overflow-y-auto flex-1 min-h-0">
          <h3 className="text-xs font-semibold text-white/40 mb-3 uppercase tracking-wider px-1">Recent Chats</h3>
          {filteredChatHistory.length > 0 ? (
            <div className="space-y-4">
              {filteredChatHistory.map((group, idx) => (
                <div key={idx}>
                  <ul className="space-y-0.5">
                    {group.chats.map((chat) => (
                      <li
                        key={chat.id}
                        className={`text-sm py-1.5 px-2 rounded-lg transition-all truncate flex items-center gap-2 ${currentChatId === chat.id
                          ? 'bg-white/10 text-white'
                          : 'text-white/60 hover:text-white/90 hover:bg-white/5'
                          }`}
                      >
                        <button
                          className="flex-1 text-left truncate"
                          onClick={() => {
                            onLoadChat && onLoadChat(chat.id);
                            if (isMobile) onToggle(); // Close sidebar on mobile after selection
                          }}
                        >
                          {chat.title}
                        </button>
                        <button
                          title="Delete chat"
                          onClick={(e) => {
                            e.stopPropagation();
                            onDeleteChat && onDeleteChat(chat.id);
                          }}
                          className="w-6 h-6 flex items-center justify-center rounded-md border border-white/10 text-white/50 hover:text-white hover:border-white/30 hover:bg-white/5 transition-all"
                        >
                          <svg
                            className="w-3.5 h-3.5"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="1.8"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          >
                            <path d="M4 7h16" />
                            <path d="M10 11v6M14 11v6" />
                            <path d="M6 7l1 12a2 2 0 002 2h6a2 2 0 002-2l1-12" />
                            <path d="M9 7V5a2 2 0 012-2h2a2 2 0 012 2v2" />
                          </svg>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-xs text-white/30 px-1 italic">
              {searchQuery ? 'No chats found' : 'No recent chats'}
            </div>
          )}
        </div>
      </div>

      {/* Mobile Overlay */}
      {isMobile && isOpen && (
        <div
          className="fixed inset-0 bg-black/60 backdrop-blur-sm z-20"
          onClick={onToggle}
        />
      )}
    </>
  );
};

export default Sidebar;
