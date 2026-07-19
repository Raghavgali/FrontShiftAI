import React, { useState } from 'react';

const MessageInput = ({ onSendMessage, isLoading, messages = [], placeholder, onOpenVoiceMode, onStop }) => {
  const [message, setMessage] = useState('');
  const hasMessages = messages.length > 0;

  const handleSubmit = (e) => {
    e.preventDefault();
    if (message.trim() && !isLoading) {
      onSendMessage(message.trim());
      setMessage('');
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  const containerClasses = hasMessages
    ? 'px-4 sm:px-8 pt-2 pb-6'
    : 'px-4 sm:px-8 pt-6 pb-6'; // Changed pb-10 to pb-6

  const formClasses = hasMessages
    ? 'w-full max-w-4xl mx-auto flex flex-col gap-4'
    : 'w-full max-w-4xl mx-auto flex flex-col gap-6';

  const composerClasses = hasMessages
    ? 'relative rounded-[22px] border border-white/10 bg-[rgba(8,10,20,0.85)] backdrop-blur-2xl p-4 sm:p-5'
    : 'relative rounded-[28px] border border-white/10 bg-[rgba(8,10,20,0.9)] backdrop-blur-2xl p-6 sm:p-7';

  const textAreaClasses = hasMessages
    ? 'w-full bg-transparent text-white/90 placeholder-white/40 text-base leading-relaxed min-h-[72px] resize-none border-none outline-none pr-48 disabled:opacity-50 disabled:cursor-not-allowed'
    : 'w-full bg-transparent text-white/90 placeholder-white/40 text-lg leading-relaxed min-h-[110px] resize-none border-none outline-none pr-48 disabled:opacity-50 disabled:cursor-not-allowed';

  const agentButtonClasses = hasMessages
    ? 'flex items-center gap-2 h-11 px-4 rounded-2xl border border-white/10 text-white/70 text-sm bg-transparent hover:bg-white/10 hover:text-white hover:border-white/20 transition-all active:scale-95'
    : 'flex items-center gap-2 h-11 px-5 rounded-2xl border border-white/10 text-white/80 text-sm bg-transparent hover:bg-white/10 hover:text-white hover:border-white/20 transition-all active:scale-95';

  return (
    <div className={`${containerClasses} transition-all duration-500`}>
      <form onSubmit={handleSubmit} className={`${formClasses} transition-all duration-500`}>
        <div className={`${composerClasses} transition-all duration-500`}>
          <textarea
            rows={3}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder || "Message AI Chat..."}
            disabled={isLoading}
            className={`${textAreaClasses} transition-all duration-500`}
          />

          <div className="absolute bottom-4 right-4 flex items-center gap-2 flex-wrap justify-end">
            <button
              type="button"
              onClick={onOpenVoiceMode}
              className="w-11 h-11 flex items-center justify-center rounded-2xl border border-white/10 text-white/60 bg-white/5 hover:text-white hover:border-white/25 hover:bg-white/10 transition-all active:scale-95"
              title="Voice mode"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
              </svg>
            </button>

            {isLoading ? (
              <button
                type="button"
                onClick={onStop}
                className="w-11 h-11 flex items-center justify-center rounded-2xl bg-red-500/20 text-red-400 border border-red-500/30 hover:bg-red-500/30 hover:border-red-500/50 shadow-[0_12px_25px_rgba(255,50,50,0.15)] transition-all active:scale-95"
                title="Stop generation"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            ) : (
              <button
                type="submit"
                disabled={!message.trim()}
                className="w-11 h-11 flex items-center justify-center rounded-2xl bg-gradient-to-br from-white/90 to-white/60 text-black/70 shadow-[0_12px_25px_rgba(15,15,20,0.55)] disabled:opacity-40 disabled:cursor-not-allowed hover:shadow-[0_15px_30px_rgba(15,15,20,0.65)] transition-all active:scale-95"
                title="Send message"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 12l14-7-7 14-2-5-5-2z" />
                </svg>
              </button>
            )}
          </div>
        </div>
      </form>
    </div>
  );
};

export default MessageInput;