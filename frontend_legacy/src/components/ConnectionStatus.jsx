import React, { useState, useEffect } from 'react';
import { healthCheck } from '../services/api';

const ConnectionStatus = () => {
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const checkConnection = async () => {
      try {
        await healthCheck();
        setConnected(true);
      } catch (error) {
        setConnected(false);
      }
    };

    checkConnection();
    const interval = setInterval(checkConnection, 30000);
    return () => clearInterval(interval);
  }, []);

  if (!connected) {
    return (
      <div className="fixed bottom-4 right-4 px-4 py-2 bg-red-500/10 border border-red-500/30 rounded-lg text-xs text-red-300 backdrop-blur-xl z-50">
        <div className="flex items-center space-x-2">
          <div className="w-2 h-2 bg-red-400 rounded-full animate-pulse"></div>
          <span>Backend Offline</span>
        </div>
      </div>
    );
  }

  return null;
};

export default ConnectionStatus;