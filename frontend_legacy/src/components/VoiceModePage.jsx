import React, { useState, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Mic, RotateCw } from 'lucide-react';
import SpeechOrb from './SpeechOrb';
import { useLiveKitVoice } from '../hooks/useLiveKitVoice';

const VoiceModePage = ({ onBackToChat, onClose }) => {
  // Get user info for session creation
  const userEmail = localStorage.getItem('user_email');
  const company = localStorage.getItem('user_company');
  
  // Track if session has been started by user
  const [hasStarted, setHasStarted] = useState(false);

  // LiveKit voice agent hook
  const {
    status,
    isConnected,
    isConnecting,
    error,
    transcript,
    partialText,
    connect,
    disconnect,
  } = useLiveKitVoice({
    userEmail,
    company,
  });

  // Handle mic button click - starts the voice session
  const handleMicClick = async () => {
    if (!hasStarted && !isConnected && !isConnecting) {
      // First click - start the session
      console.log('🎙️ Starting voice session...');
      setHasStarted(true);
      await connect();
    } else if (isConnected) {
      // Already connected - could toggle mute here if needed
      console.log('Mic clicked - status:', status);
    }
  };

  // Restart the session
  const handleRestart = async () => {
    await disconnect();
    setHasStarted(false);
  };

  // Close and cleanup
  const handleClose = async () => {
    await disconnect();
    setHasStarted(false);
    onClose();
  };

  const getMicButtonText = () => {
    if (!hasStarted) {
      return 'Tap to start';
    }
    if (isConnecting) {
      return 'Connecting...';
    }
    switch (status) {
      case 'listening':
        return 'Listening...';
      case 'processing':
        return 'Processing...';
      case 'speaking':
        return 'Agent speaking...';
      default:
        return 'Listening...';
    }
  };

  const getStatusMessage = () => {
    if (!hasStarted) {
      return 'Tap the microphone to start talking';
    }
    if (isConnecting) {
      return 'Connecting to voice agent...';
    }
    if (error) {
      return error;
    }
    if (isConnected) {
      return 'Connected - Speak naturally';
    }
    return 'Ready to connect';
  };

  // Split text into confirmed and pending parts
  const renderTranscriptionText = () => {
    if (!partialText && !transcript) return null;

    const displayText = partialText || transcript;
    const words = displayText.split(' ');
    const confirmedWords = status === 'listening' ? words.slice(0, -1) : words;
    const pendingWord = status === 'listening' ? words[words.length - 1] : '';

    return (
      <div className="mt-8 max-w-2xl mx-auto px-4">
        <div className="text-center">
          {confirmedWords.length > 0 && (
            <span className="text-white font-semibold text-lg">
              {confirmedWords.join(' ')}
            </span>
          )}
          {pendingWord && (
            <>
              <span className="text-white font-semibold text-lg"> {pendingWord}</span>
              <span className="text-white/60 text-lg">|</span>
            </>
          )}
          {status === 'listening' && !pendingWord && confirmedWords.length > 0 && (
            <span className="text-white/60 text-lg">|</span>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="min-h-screen w-full bg-gradient-to-br from-[#0a0a0f] via-[#1a1a24] to-[#0a0a0f] text-white flex flex-col relative overflow-hidden">
      {/* Top Bar */}
      <div className="w-full px-6 py-6 flex items-center justify-between relative z-20">
        {/* Left: App Name */}
        <div className="flex flex-col">
          <h1 className="text-xl font-bold text-white">FrontShiftAI</h1>
          <p className="text-sm text-white/60">Voice Mode</p>
        </div>

        {/* Right: Action Buttons */}
        <div className="flex items-center gap-3">
          <button
            onClick={handleClose}
            className="w-10 h-10 rounded-full border border-white/10 bg-white/5 hover:bg-white/10 transition-all flex items-center justify-center"
            title="Close"
          >
            <X size={20} className="text-white/70" />
          </button>
        </div>
      </div>

      {/* Center Section - Speech Orb */}
      <div className="flex-1 flex flex-col items-center justify-center px-6 relative">
        {/* Connection Status Badge */}
        <div className="absolute top-20 left-1/2 transform -translate-x-1/2 z-30">
          {isConnecting && (
            <div className="bg-white/10 backdrop-blur-xl border border-white/20 rounded-full px-6 py-3 flex items-center gap-3">
              <div className="w-4 h-4 border-2 border-white/60 border-t-transparent rounded-full animate-spin"></div>
              <span className="text-white/80 text-sm">Connecting...</span>
            </div>
          )}

          {error && (
            <div className="bg-red-500/20 backdrop-blur-xl border border-red-500/40 rounded-full px-6 py-3 flex items-center gap-3 max-w-md">
              <svg className="w-5 h-5 text-red-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <span className="text-red-200 text-sm truncate">{error}</span>
            </div>
          )}

          {isConnected && !error && !isConnecting && (
            <div className="bg-green-500/20 backdrop-blur-xl border border-green-500/40 rounded-full px-6 py-3 flex items-center gap-3">
              <div className="w-3 h-3 bg-green-400 rounded-full animate-pulse"></div>
              <span className="text-green-200 text-sm">Connected - Speak naturally</span>
            </div>
          )}

          {!hasStarted && !isConnecting && !error && (
            <div className="bg-white/5 backdrop-blur-xl border border-white/10 rounded-full px-6 py-3 flex items-center gap-3">
              <div className="w-3 h-3 bg-white/40 rounded-full"></div>
              <span className="text-white/60 text-sm">Tap microphone to start</span>
            </div>
          )}
        </div>

        {/* 3D Speech Orb */}
        <div className="mb-8">
          <SpeechOrb status={hasStarted ? status : 'idle'} size={320} />
        </div>

        {/* Real-time Transcription Display */}
        <AnimatePresence>
          {(partialText || transcript) && (
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
              className="w-full"
            >
              {renderTranscriptionText()}
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Bottom Control Strip */}
      <div className="pb-10 flex flex-col items-center gap-4 relative z-20">
        <div className="flex items-center justify-center gap-6 w-full max-w-md px-6">
          {/* Restart Button (Left) - Only show when session started */}
          <motion.button
            onClick={handleRestart}
            whileHover={{ scale: 1.1 }}
            whileTap={{ scale: 0.95 }}
            className={`w-12 h-12 rounded-full border border-white/10 bg-white/5 hover:bg-white/10 transition-all flex items-center justify-center ${
              !hasStarted ? 'opacity-30 cursor-not-allowed' : ''
            }`}
            disabled={!hasStarted}
            title="Restart"
          >
            <RotateCw size={20} className="text-white/70" />
          </motion.button>

          {/* Microphone Button (Center) */}
          <motion.button
            onClick={handleMicClick}
            disabled={isConnecting}
            whileHover={{ scale: !isConnecting ? 1.1 : 1 }}
            whileTap={{ scale: !isConnecting ? 0.95 : 1 }}
            className={`w-16 h-16 md:w-20 md:h-20 rounded-full flex items-center justify-center transition-all ${
              isConnecting
                ? 'bg-gradient-to-r from-[#6B7280] to-[#4B5563] opacity-70 cursor-wait'
                : !hasStarted
                ? 'bg-gradient-to-r from-[#9CA3AF] to-[#6B7280] shadow-[0_0_40px_rgba(156,163,175,0.4)] hover:shadow-[0_0_60px_rgba(156,163,175,0.6)] cursor-pointer'
                : status === 'listening'
                ? 'bg-gradient-to-r from-[#22c55e] to-[#16a34a] shadow-[0_0_60px_rgba(34,197,94,0.6)] ring-2 ring-green-400'
                : status === 'speaking'
                ? 'bg-gradient-to-r from-[#3b82f6] to-[#2563eb] shadow-[0_0_60px_rgba(59,130,246,0.6)] ring-2 ring-blue-400'
                : status === 'processing'
                ? 'bg-gradient-to-r from-[#f59e0b] to-[#d97706] shadow-[0_0_60px_rgba(245,158,11,0.6)] ring-2 ring-yellow-400'
                : 'bg-gradient-to-r from-[#9CA3AF] to-[#6B7280] shadow-[0_0_40px_rgba(156,163,175,0.4)]'
            }`}
            title={getMicButtonText()}
          >
            {isConnecting ? (
              <div className="w-7 h-7 border-3 border-white/60 border-t-transparent rounded-full animate-spin"></div>
            ) : (
              <Mic 
                size={28} 
                className={`text-white ${status === 'listening' && hasStarted ? 'animate-pulse' : ''}`}
              />
            )}
          </motion.button>

          {/* Close Button (Right) */}
          <motion.button
            onClick={handleClose}
            whileHover={{ scale: 1.1 }}
            whileTap={{ scale: 0.95 }}
            className="w-12 h-12 rounded-full border border-white/10 bg-white/5 hover:bg-white/10 transition-all flex items-center justify-center"
            title="Close"
          >
            <X size={20} className="text-white/70" />
          </motion.button>
        </div>

        {/* Button Label */}
        <p className="text-sm text-white/60">{getMicButtonText()}</p>

        {/* Privacy Notice */}
        <p className="text-xs text-white/40 text-center px-6 max-w-md">
          Your voice may be recorded to improve the assistant.
        </p>
      </div>
    </div>
  );
};

export default VoiceModePage;
