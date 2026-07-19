import React, { useState } from 'react';
import ChatArea from './ChatArea';
import PTORequestsTab from './PTORequestsTab';
import HRTicketsTab from './HRTicketsTab';
import MessageInput from './MessageInput';
import VoiceModePage from './VoiceModePage';

const UserChatPage = ({
  messages,
  isLoading,
  onSendMessage,
  userInfo,
  isSidebarOpen,
  onOpenSidebar,
  onStop
}) => {
  const [activeTab, setActiveTab] = useState('chat');
  const [isVoiceModeOpen, setIsVoiceModeOpen] = useState(false);

  // If voice mode is open, show only the voice mode page
  if (isVoiceModeOpen) {
    return (
      <VoiceModePage
        onBackToChat={() => setIsVoiceModeOpen(false)}
        onClose={() => setIsVoiceModeOpen(false)}
      />
    );
  }

  return (
    <div className="flex-1 flex flex-col h-full relative min-h-0">
      {/* Tab Navigation */}
      <div className="flex items-center px-6 py-4 border-b border-white/10 bg-black/10 backdrop-blur-xl sticky top-0 z-10">
        <div className="flex items-center space-x-3">
          {!isSidebarOpen && (
            <button
              onClick={() => onOpenSidebar && onOpenSidebar()}
              className="w-8 h-8 bg-[#1a1a24] text-white/70 hover:text-white border border-white/10 rounded-full flex items-center justify-center cursor-pointer hover:bg-white/10 transition-all shadow-lg"
              title="Open Sidebar"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
            </button>
          )}
          {/* Navigation tabs */}
          <div className="flex items-center space-x-2">
            <button
              onClick={() => setActiveTab('chat')}
              className={`px-4 py-2 rounded-lg text-sm transition-all ${activeTab === 'chat'
                  ? 'bg-white/10 border border-white/10 text-white'
                  : 'bg-white/5 border border-white/10 text-white/60 hover:bg-white/10 hover:text-white/80'
                }`}
            >
              Chat
            </button>
            <button
              onClick={() => setActiveTab('pto')}
              className={`px-4 py-2 rounded-lg text-sm transition-all ${activeTab === 'pto'
                  ? 'bg-white/10 border border-white/10 text-white'
                  : 'bg-white/5 border border-white/10 text-white/60 hover:bg-white/10 hover:text-white/80'
                }`}
            >
              PTO Requests
            </button>
            <button
              onClick={() => setActiveTab('hr')}
              className={`px-4 py-2 rounded-lg text-sm transition-all ${activeTab === 'hr'
                  ? 'bg-white/10 border border-white/10 text-white'
                  : 'bg-white/5 border border-white/10 text-white/60 hover:bg-white/10 hover:text-white/80'
                }`}
            >
              HR Tickets
            </button>
          </div>
        </div>
      </div>

      {/* Tab Content */}
      <div className="flex-1 flex flex-col min-h-0">
        {activeTab === 'chat' && (
          <>
            <ChatArea messages={messages} isLoading={isLoading} />
            <MessageInput
              onSendMessage={onSendMessage}
              isLoading={isLoading}
              messages={messages}
              placeholder="Ask about PTO, HR policies, benefits, or schedule a meeting..."
              onOpenVoiceMode={() => setIsVoiceModeOpen(true)}
              onStop={onStop}
            />
          </>
        )}

        {activeTab === 'pto' && <PTORequestsTab userInfo={userInfo} />}

        {activeTab === 'hr' && <HRTicketsTab userInfo={userInfo} />}
      </div>
    </div>
  );
};

export default UserChatPage;
