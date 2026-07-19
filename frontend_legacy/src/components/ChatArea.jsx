import React, { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const MarkdownComponents = {
  table: ({ children }) => (
    <div className="overflow-x-auto my-4 rounded-lg border border-white/10">
      <table className="min-w-full divide-y divide-white/10 bg-white/5 backdrop-blur-sm">
        {children}
      </table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-white/10">{children}</thead>,
  tbody: ({ children }) => <tbody className="divide-y divide-white/10">{children}</tbody>,
  tr: ({ children }) => <tr className="hover:bg-white/5 transition-colors">{children}</tr>,
  th: ({ children }) => (
    <th className="px-4 py-3 text-left text-xs font-semibold text-white uppercase tracking-wider">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="px-4 py-3 whitespace-pre-wrap text-sm text-white/80 border-l border-white/5 first:border-l-0">
      {children}
    </td>
  ),
  ul: ({ children }) => <ul className="list-disc list-inside space-y-1 my-2 text-white/90 ml-2">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal list-inside space-y-1 my-2 text-white/90 ml-2">{children}</ol>,
  li: ({ children }) => <li className="text-white/80 leading-relaxed">{children}</li>,
  p: ({ children }) => <p className="text-white/90 mb-2 last:mb-0 leading-relaxed">{children}</p>,
  h1: ({ children }) => <h1 className="text-2xl font-bold text-white mb-4 mt-2 border-b border-white/10 pb-2">{children}</h1>,
  h2: ({ children }) => <h2 className="text-xl font-bold text-white mb-3 mt-4 border-b border-white/10 pb-2">{children}</h2>,
  h3: ({ children }) => <h3 className="text-lg font-semibold text-white mb-2 mt-3">{children}</h3>,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:text-blue-300 underline decoration-blue-400/50 hover:decoration-blue-300">
      {children}
    </a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-4 border-white/20 pl-4 py-1 my-4 italic text-white/60 bg-white/5 rounded-r">
      {children}
    </blockquote>
  ),
  code: ({ node, inline, className, children, ...props }) => {
    const match = /language-(\w+)/.exec(className || '');
    return !inline ? (
      <div className="relative my-4 rounded-lg overflow-hidden bg-[#0d1117] border border-white/10">
        <div className="flex items-center justify-between px-4 py-2 bg-white/5 border-b border-white/10">
          <span className="text-xs text-white/40 font-mono">{match ? match[1] : 'code'}</span>
        </div>
        <pre className="p-4 overflow-x-auto scrollbar-thin scrollbar-thumb-white/10 scrollbar-track-transparent">
          <code className={`${className} text-sm font-mono text-white/80`} {...props}>
            {children}
          </code>
        </pre>
      </div>
    ) : (
      <code className="bg-white/10 rounded px-1.5 py-0.5 text-sm font-mono text-white/90 border border-white/10" {...props}>
        {children}
      </code>
    );
  }
};

const Typewriter = ({ content, onComplete }) => {
  const [displayedContent, setDisplayedContent] = useState('');

  useEffect(() => {
    let currentIndex = 0;
    // Calculate speed based on content length - faster for longer content
    // Range: 5ms to 30ms per char
    const speed = Math.max(5, Math.min(30, 1000 / (content.length || 1)));

    // Clear initial state when content changes meaningfully to avoid weird overwrites if re-used
    setDisplayedContent('');

    const interval = setInterval(() => {
      if (currentIndex < content.length) {
        setDisplayedContent(prev => content.slice(0, currentIndex + 1));
        currentIndex++;
      } else {
        clearInterval(interval);
        if (onComplete) onComplete();
      }
    }, speed);

    return () => clearInterval(interval);
  }, [content, onComplete]);

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={MarkdownComponents}
    >
      {displayedContent}
    </ReactMarkdown>
  );
};

const ChatArea = ({ messages, isLoading }) => {
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isLoading]);

  // Auto-scroll during generation/typing
  useEffect(() => {
    const interval = setInterval(() => {
      if (isLoading) {
        scrollToBottom();
      }
    }, 100);
    return () => clearInterval(interval);
  }, [isLoading]);

  const getDynamicGreeting = () => {
    const name = localStorage.getItem('user_name') || 'there';
    const greetings = [
      `${name} returns! Woohoo!`,
      `Welcome back, ${name}!`,
      `Great to see you, ${name}!`,
      `Hello ${name}, ready to work?`,
      `Good to have you back, ${name}!`
    ];
    const index = Math.floor(Math.random() * greetings.length);
    return greetings[index];
  };

  const [greeting, setGreeting] = React.useState('');

  useEffect(() => {
    setGreeting(getDynamicGreeting());
  }, []);

  const pinnedMessage = messages.length > 0 ? messages[0] : null;
  const conversationMessages = pinnedMessage ? messages.slice(1) : [];

  const renderMessageBubble = (message, index, isLast) => {
    const isAssistant = message.role === 'assistant';

    // Animate if:
    // 1. It is the last message
    // 2. It is from the assistant
    // 3. It is NOT loading (i.e. message is complete)
    // 4. It is recent (less than 1 min old) to avoid animating history on reload
    const isRecent = (Date.now() - (message.timestamp || 0)) < 60000;
    const shouldAnimate = isLast && isAssistant && !isLoading && isRecent;

    return (
      <div
        key={index}
        className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
      >
        <div
          className={`glass-card max-w-2xl px-6 py-4 ${message.role === 'user'
            ? 'bg-white/15 border-white/20'
            : 'bg-white/10 border-white/10'
            }`}
        >
          <div className="space-y-2 text-white/90">
            {shouldAnimate ? (
              <Typewriter
                content={message.content}
                onComplete={scrollToBottom}
              />
            ) : (
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={MarkdownComponents}
              >
                {message.content}
              </ReactMarkdown>
            )}
          </div>
          {message.sources && message.sources.length > 0 && (
            <div className="mt-3 pt-3 border-t border-white/10">
              <p className="text-xs text-white/50 mb-2">Sources:</p>
              <div className="space-y-1">
                {message.sources.map((source, idx) => (
                  <p key={idx} className="text-xs text-white/40">
                    • {source.filename}
                  </p>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="flex-1 p-8 relative min-h-0 overflow-hidden flex flex-col">
      {/* Floating Orb Background */}
      <div className="absolute top-1/4 left-1/2 transform -translate-x-1/2 -translate-y-1/2 w-96 h-96 orb-gradient rounded-full blur-3xl opacity-30 animate-float-orb pointer-events-none z-0"></div>

      {messages.length === 0 ? (
        /* Empty State */
        <div className="relative z-10 flex flex-col items-center pt-16 flex-1">
          {/* Central Orb */}
          <div className="mb-8 relative">
            <div className="w-32 h-32 rounded-full bg-gradient-to-br from-white/20 via-white/10 to-black/20 blur-2xl animate-pulse-glow absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2"></div>
            <div className="w-24 h-24 rounded-full bg-gradient-to-br from-white/20 to-white/5 backdrop-blur-xl border border-white/10 shadow-[0_0_30px_rgba(255,255,255,0.1)] relative z-10 flex items-center justify-center">
              <div className="w-16 h-16 rounded-full bg-gradient-to-br from-white/30 to-gray-500/20 blur-xl"></div>
            </div>
          </div>
          <h3 className="text-3xl font-light text-white/90 mb-2 text-center">
            {greeting}. Can I help you with anything?
          </h3>
        </div>
      ) : (
        /* Messages Container */
        <div className="relative z-10 flex flex-col h-full">
          {pinnedMessage && (
            <div className="max-w-4xl mx-auto w-full flex-shrink-0">
              {/* Reuse render bubble logic for pinned message, forcing no animation */}
              {renderMessageBubble(pinnedMessage, 'pinned', false)}
            </div>
          )}
          <div className={`${conversationMessages.length > 0 || isLoading ? 'flex-1 overflow-y-auto mt-6' : 'flex-1'}`}>
            <div className="max-w-4xl mx-auto space-y-6">
              {conversationMessages.map((message, index) =>
                renderMessageBubble(message, index, index === conversationMessages.length - 1)
              )}
              {isLoading && (
                <div className="flex justify-start">
                  <div className="glass-card px-6 py-4 bg-white/10">
                    <div className="flex space-x-2">
                      <div className="w-2 h-2 bg-white/60 rounded-full animate-bounce"></div>
                      <div className="w-2 h-2 bg-white/60 rounded-full animate-bounce" style={{ animationDelay: '0.1s' }}></div>
                      <div className="w-2 h-2 bg-white/60 rounded-full animate-bounce" style={{ animationDelay: '0.2s' }}></div>
                    </div>
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default ChatArea;