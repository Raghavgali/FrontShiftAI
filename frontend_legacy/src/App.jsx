import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { Toaster } from 'sonner';
import Sidebar from './components/Sidebar';
import UserChatPage from './components/UserChatPage';
import ConnectionStatus from './components/ConnectionStatus';
import Login from './components/Login';
import LandingPage from './components/LandingPage';
import SuperAdminDashboard from './components/SuperAdminDashboard';
import CompanyAdminDashboard from './components/CompanyAdminDashboard';
import { logout, getUserInfo } from './services/api';

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [userInfo, setUserInfo] = useState(null);
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);
  const [showLogin, setShowLogin] = useState(false);

  const [activeView, setActiveView] = useState('home');
  const [messages, setMessages] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    const saved = localStorage.getItem('sidebarWidth');
    return saved ? parseInt(saved, 10) : 320;
  });
  const [isResizing, setIsResizing] = useState(false);
  const [currentChatId, setCurrentChatId] = useState(null);
  const [chatHistory, setChatHistory] = useState([]);
  const abortControllerRef = useRef(null);

  // Sidebar state
  const [isSidebarOpen, setIsSidebarOpen] = useState(window.innerWidth >= 768);
  const [isMobile, setIsMobile] = useState(window.innerWidth < 768);

  const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  // Check authentication on mount
  useEffect(() => {
    const checkAuth = async () => {
      const token = localStorage.getItem('access_token');
      const email = localStorage.getItem('user_email');

      if (token && email) {
        try {
          const userData = await getUserInfo();
          if (!userData.name) {
            // Fallback if name is missing in response but saved locally or in token
            userData.name = localStorage.getItem('user_name') || 'User';
          }
          setUserInfo(userData);
          setIsAuthenticated(true);
        } catch (error) {
          console.error('Token validation failed:', error);
          logout();
          setIsAuthenticated(false);
        }
      } else {
        setIsAuthenticated(false);
      }

      setIsCheckingAuth(false);
    };

    checkAuth();
  }, []);

  // Handle window resize for responsive layout
  useEffect(() => {
    const handleResize = () => {
      const mobile = window.innerWidth < 768;
      setIsMobile(mobile);
      if (mobile && isSidebarOpen) {
        setIsSidebarOpen(false); // Auto-close on switch to mobile
      } else if (!mobile && !isSidebarOpen && window.innerWidth >= 1024) {
        // Optional: Auto-open on very large screens if preferred, 
        // but let's respect user choice mostly.
        // For now, let's just update isMobile.
      }
    };

    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [isSidebarOpen]);

  // Load chat history from database when authenticated
  useEffect(() => {
    if (isAuthenticated && userInfo?.role === 'user') {
      loadChatHistory();
    }
  }, [isAuthenticated, userInfo]);

  const loadChatHistory = async () => {
    try {
      const token = localStorage.getItem('access_token');
      const response = await axios.get(
        `${API_BASE_URL}/api/chat/conversations`,
        { headers: { Authorization: `Bearer ${token}` } }
      );

      setChatHistory(response.data);
    } catch (error) {
      console.error('Error loading chat history:', error);
    }
  };

  useEffect(() => {
    localStorage.setItem('sidebarWidth', sidebarWidth.toString());
  }, [sidebarWidth]);

  const handleLoginSuccess = (loginData) => {
    setUserInfo({
      email: loginData.email,
      name: loginData.name,
      company: loginData.company,
      role: loginData.role
    });
    setIsAuthenticated(true);
    // Open sidebar by default on desktop login
    if (window.innerWidth >= 768) setIsSidebarOpen(true);
  };

  const handleLogout = () => {
    logout();
    setIsAuthenticated(false);
    setUserInfo(null);
    setMessages([]);
    setChatHistory([]);
    setCurrentChatId(null);
  };

  const handleNewChat = () => {
    setCurrentChatId(null);
    setMessages([]);
    if (isMobile) {
      setIsSidebarOpen(false);
    }
  };

  const handleLoadChat = async (chatId) => {
    try {
      const token = localStorage.getItem('access_token');
      const response = await axios.get(
        `${API_BASE_URL}/api/chat/conversations/${chatId}/messages`,
        { headers: { Authorization: `Bearer ${token}` } }
      );

      // Convert database format to frontend format
      const loadedMessages = response.data.map(msg => ({
        role: msg.role,
        content: msg.content,
        agentType: msg.agent_type,
        timestamp: new Date(msg.created_at).getTime()
      }));

      setCurrentChatId(chatId);
      setMessages(loadedMessages);
      if (isMobile) {
        setIsSidebarOpen(false);
      }
    } catch (error) {
      console.error('Error loading chat:', error);
    }
  };

  const handleDeleteChat = async (chatId) => {
    try {
      const token = localStorage.getItem('access_token');
      await axios.delete(
        `${API_BASE_URL}/api/chat/conversations/${chatId}`,
        { headers: { Authorization: `Bearer ${token}` } }
      );

      // Remove from local state
      setChatHistory(prev => prev.filter(chat => chat.id !== chatId));

      if (currentChatId === chatId) {
        setCurrentChatId(null);
        setMessages([]);
      }
    } catch (error) {
      console.error('Error deleting chat:', error);
    }
  };

  const getTimeLabel = (timestamp) => {
    const now = new Date();
    const chatDate = new Date(timestamp);
    const diffTime = Math.abs(now - chatDate);
    const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));

    if (diffDays === 0) return 'Today';
    if (diffDays === 1) return 'Yesterday';
    if (diffDays <= 7) return 'Previous 7 Days';
    if (diffDays <= 30) return 'Previous 30 Days';
    return '30 Days +';
  };

  const groupedChats = chatHistory.reduce((groups, chat) => {
    const timeLabel = getTimeLabel(chat.updated_at);
    if (!groups[timeLabel]) {
      groups[timeLabel] = [];
    }
    groups[timeLabel].push(chat);
    return groups;
  }, {});

  const formattedChatHistory = Object.entries(groupedChats).map(([time, chats]) => ({
    time,
    chats: chats.map(chat => ({
      id: chat.id,
      title: chat.title,
      timestamp: new Date(chat.updated_at).getTime()
    }))
  })).sort((a, b) => {
    const aTime = a.chats[0]?.timestamp || 0;
    const bTime = b.chats[0]?.timestamp || 0;
    return bTime - aTime;
  });

  useEffect(() => {
    const handleMouseMove = (e) => {
      if (!isResizing) return;
      e.preventDefault();
      const newWidth = Math.min(Math.max(240, e.clientX), 600);
      setSidebarWidth(newWidth);
    };

    const handleMouseUp = () => {
      setIsResizing(false);
    };

    if (isResizing) {
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      document.body.style.pointerEvents = 'auto';
    } else {
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    }

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      if (!isResizing) {
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      }
    };
  }, [isResizing]);

  const handleStopGeneration = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
      setIsLoading(false);
      console.log('🛑 Generation stopped by user');
    }
  };

  const handleSendMessage = async (message) => {
    const userMessage = {
      role: 'user',
      content: message,
      timestamp: Date.now(),
    };
    const updatedMessages = [...messages, userMessage];
    setMessages(updatedMessages);
    setIsLoading(true);

    try {
      console.log('📤 Sending message to smart router:', message);

      // Create new abort controller for this request
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      abortControllerRef.current = new AbortController();

      const token = localStorage.getItem('access_token');
      const response = await axios.post(
        `${API_BASE_URL}/api/chat/message`,
        {
          message,
          conversation_id: currentChatId
        },
        {
          headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json'
          },
          signal: abortControllerRef.current.signal
        }
      );

      console.log('📥 Received response from', response.data.agent_used, 'agent');

      // Update conversation ID if new
      if (!currentChatId) {
        setCurrentChatId(response.data.conversation_id);
      }

      const assistantMessage = {
        role: 'assistant',
        content: response.data.response,
        agentType: response.data.agent_used,
        ...(response.data.agent_used === 'pto' && response.data.metadata.balance_info && {
          ptoInfo: {
            requestCreated: response.data.metadata.request_created,
            requestId: response.data.metadata.request_id,
            balanceInfo: response.data.metadata.balance_info
          }
        }),
        ...(response.data.agent_used === 'hr_ticket' && response.data.metadata.ticket_id && {
          hrTicketInfo: {
            ticketCreated: response.data.metadata.ticket_created,
            ticketId: response.data.metadata.ticket_id,
            queuePosition: response.data.metadata.queue_position
          }
        }),
        timestamp: Date.now(),
      };
      const finalMessages = [...updatedMessages, assistantMessage];
      setMessages(finalMessages);

      // Reload chat history to get updated list
      await loadChatHistory();

    } catch (error) {
      console.error('❌ Error sending message:', error);

      if (axios.isCancel(error)) {
        errorMsg = 'Generation stopped by user.';
      } else if (error.response?.status === 401 || error.message === 'Not authenticated') {
        errorMsg = '🔒 Session expired. Please log in again.';
        setTimeout(() => handleLogout(), 2000);
      } else if (error.code === 'ECONNREFUSED' || error.message.includes('Network Error')) {
        errorMsg = '🔌 Cannot connect to backend. Please ensure the backend server is running on port 8000.';
      } else if (error.response?.data?.detail) {
        errorMsg = `Backend error: ${error.response.data.detail}`;
      }

      const errorMessage = {
        role: 'assistant',
        content: errorMsg,
        timestamp: Date.now(),
      };
      const finalMessages = [...updatedMessages, errorMessage];
      setMessages(finalMessages);

    } finally {
      setIsLoading(false);
    }
  };

  if (isCheckingAuth) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-[#0a0a0f] via-[#1a1a24] to-[#0a0a0f] flex items-center justify-center">
        <div className="text-white/60">Loading...</div>
      </div>
    );
  }

  if (!isAuthenticated) {
    if (showLogin) {
      return <Login onLoginSuccess={handleLoginSuccess} onBack={() => setShowLogin(false)} />;
    }
    return <LandingPage onGetStarted={() => setShowLogin(true)} />;
  }

  if (userInfo?.role === 'super_admin') {
    return <SuperAdminDashboard onLogout={handleLogout} userInfo={userInfo} />;
  }

  if (userInfo?.role === 'company_admin') {
    return <CompanyAdminDashboard onLogout={handleLogout} userInfo={userInfo} />;
  }

  // Regular user - show chat interface
  return (
    <div className="min-h-screen bg-gradient-to-br from-[#0a0a0f] via-[#1a1a24] to-[#0a0a0f] relative overflow-hidden">
      <div className="fixed top-1/4 right-1/4 w-96 h-96 bg-gradient-to-r from-white/10 to-gray-500/10 rounded-full blur-3xl opacity-20 animate-float-orb pointer-events-none z-0"></div>

      <div className="relative z-10 flex min-h-screen">
        <Sidebar
          activeView={activeView}
          setActiveView={setActiveView}
          width={sidebarWidth}
          chatHistory={formattedChatHistory}
          onNewChat={handleNewChat}
          onLoadChat={handleLoadChat}
          onDeleteChat={handleDeleteChat}
          currentChatId={currentChatId}
          userInfo={userInfo}
          onLogout={handleLogout}
          isOpen={isSidebarOpen}
          onToggle={() => setIsSidebarOpen(!isSidebarOpen)}
          isMobile={isMobile}
        />

        {/* Resizer - only visible on desktop when sidebar is open */}
        {!isMobile && isSidebarOpen && (
          <div
            className={`fixed top-0 h-screen w-3 cursor-col-resize z-20 transition-all ${isResizing ? 'bg-white/10' : ''
              }`}
            style={{ left: `${sidebarWidth - 1}px` }}
            onMouseDown={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setIsResizing(true);
            }}
          >
            <div className={`absolute inset-y-0 left-1/2 transform -translate-x-1/2 w-0.5 transition-colors ${isResizing ? 'bg-white/40' : 'bg-white/10 hover:bg-white/30'
              }`}></div>
          </div>
        )}

        {isResizing && (
          <div className="fixed inset-0 bg-black/0 z-[15] cursor-col-resize" />
        )}

        <div
          className="flex-1 flex flex-col min-h-screen transition-all duration-300 ease-in-out"
          style={{
            marginLeft: isMobile || !isSidebarOpen ? '0px' : `${sidebarWidth}px`,
            width: isMobile || !isSidebarOpen ? '100%' : `calc(100% - ${sidebarWidth}px)`
          }}
        >
          <UserChatPage
            messages={messages}
            isLoading={isLoading}
            onSendMessage={handleSendMessage}
            userInfo={userInfo}
            isSidebarOpen={isSidebarOpen}
            onOpenSidebar={() => setIsSidebarOpen(true)}
            onStop={handleStopGeneration}
          />
        </div>
      </div>

      <ConnectionStatus />
      <Toaster position="top-right" theme="dark" richColors />
    </div>
  );
}

export default App;
