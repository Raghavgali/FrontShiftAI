import React, { useState, useEffect } from 'react';
import { getPTOBalance, getPTORequests } from '../services/api';

const PTORequestsTab = ({ userInfo }) => {
  const [balance, setBalance] = useState(null);
  const [requests, setRequests] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    setLoading(true);
    try {
      const [balanceData, requestsData] = await Promise.all([
        getPTOBalance(),
        getPTORequests()
      ]);
      setBalance(balanceData);
      setRequests(requestsData);
    } catch (error) {
      console.error('Error fetching PTO data:', error);
    } finally {
      setLoading(false);
    }
  };

  const getStatusColor = (status) => {
    switch (status) {
      case 'pending': return 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30';
      case 'approved': return 'bg-green-500/20 text-green-400 border-green-500/30';
      case 'denied': return 'bg-red-500/20 text-red-400 border-red-500/30';
      default: return 'bg-white/10 text-white/60 border-white/20';
    }
  };

  const getStatusIcon = (status) => {
    switch (status) {
      case 'pending': return '⏳';
      case 'approved': return '✅';
      case 'denied': return '❌';
      default: return '❓';
    }
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-white/60">Loading PTO information...</div>
      </div>
    );
  }

  return (
    <div className="flex-1 p-8 overflow-y-auto">
      <div className="max-w-4xl mx-auto space-y-6">
        {/* Balance Card */}
        {balance && (
          <div className="glass-card p-6 bg-white/10 border-white/10">
            <h2 className="text-xl font-semibold text-white mb-4">Leave Balance</h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <p className="text-sm text-white/50 mb-1">Available</p>
                <p className="text-2xl font-bold text-green-400">{balance.remaining_days}</p>
                <p className="text-xs text-white/40">days</p>
              </div>
              <div>
                <p className="text-sm text-white/50 mb-1">Used</p>
                <p className="text-2xl font-bold text-white/80">{balance.used_days}</p>
                <p className="text-xs text-white/40">days</p>
              </div>
              <div>
                <p className="text-sm text-white/50 mb-1">Pending</p>
                <p className="text-2xl font-bold text-yellow-400">{balance.pending_days}</p>
                <p className="text-xs text-white/40">days</p>
              </div>
              <div>
                <p className="text-sm text-white/50 mb-1">Total</p>
                <p className="text-2xl font-bold text-white">{balance.total_days}</p>
                <p className="text-xs text-white/40">days</p>
              </div>
            </div>
          </div>
        )}

        {/* Requests List */}
        <div className="glass-card p-6 bg-white/10 border-white/10">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-semibold text-white">Your PTO Requests</h2>
            <button
              onClick={fetchData}
              className="px-3 py-1.5 bg-white/10 hover:bg-white/15 border border-white/10 rounded-lg text-white/80 hover:text-white transition-all text-sm"
            >
              <svg 
                className="w-4 h-4" 
                fill="none" 
                stroke="currentColor" 
                viewBox="0 0 24 24"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
            </button>
          </div>

          {requests.length === 0 ? (
            <div className="text-center py-12">
              <div className="text-4xl mb-3">🏖️</div>
              <p className="text-white/60">No PTO requests yet</p>
              <p className="text-sm text-white/40 mt-2">Go to the Chat tab to request time off</p>
            </div>
          ) : (
            <div className="space-y-3">
              {requests.map((request) => (
                <div
                  key={request.id}
                  className="bg-white/5 border border-white/10 rounded-lg p-4 hover:bg-white/8 transition-all"
                >
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-1">
                        <h3 className="text-white font-medium">
                          {new Date(request.start_date).toLocaleDateString('en-US', { 
                            month: 'short', 
                            day: 'numeric',
                            year: 'numeric'
                          })} 
                          {' - '}
                          {new Date(request.end_date).toLocaleDateString('en-US', { 
                            month: 'short', 
                            day: 'numeric',
                            year: 'numeric'
                          })}
                        </h3>
                      </div>
                      <p className="text-sm text-white/60">
                        {request.days_requested} {request.days_requested === 1 ? 'day' : 'days'}
                      </p>
                    </div>
                    <span className={`px-3 py-1 rounded-full text-xs font-medium border ${getStatusColor(request.status)}`}>
                      {getStatusIcon(request.status)} {request.status}
                    </span>
                  </div>

                  {request.reason && (
                    <div className="mt-2 pt-2 border-t border-white/10">
                      <p className="text-xs text-white/50 mb-1">Reason:</p>
                      <p className="text-sm text-white/80">{request.reason}</p>
                    </div>
                  )}

                  {request.admin_notes && (
                    <div className="mt-2 pt-2 border-t border-white/10">
                      <p className="text-xs text-white/50 mb-1">Admin Notes:</p>
                      <p className="text-sm text-white/80">{request.admin_notes}</p>
                    </div>
                  )}

                  <div className="mt-3 flex items-center justify-between text-xs text-white/40">
                    <span>Requested: {new Date(request.created_at).toLocaleDateString()}</span>
                    {request.reviewed_at && (
                      <span>Reviewed: {new Date(request.reviewed_at).toLocaleDateString()}</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default PTORequestsTab;