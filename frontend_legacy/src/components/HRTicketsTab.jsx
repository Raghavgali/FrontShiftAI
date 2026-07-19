import React, { useState, useEffect } from 'react';
import { getMyHRTickets } from '../services/api';

const HRTicketsTab = ({ userInfo }) => {
  const [tickets, setTickets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedTicket, setExpandedTicket] = useState(null);

  useEffect(() => {
    fetchTickets();
  }, []);

  const fetchTickets = async () => {
    setLoading(true);
    try {
      const response = await getMyHRTickets();
      setTickets(response.tickets || []);
    } catch (error) {
      console.error('Error fetching HR tickets:', error);
    } finally {
      setLoading(false);
    }
  };

  const getStatusColor = (status) => {
    switch (status) {
      case 'pending': return 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30';
      case 'in_progress': return 'bg-blue-500/20 text-blue-400 border-blue-500/30';
      case 'scheduled': return 'bg-purple-500/20 text-purple-400 border-purple-500/30';
      case 'resolved': return 'bg-green-500/20 text-green-400 border-green-500/30';
      case 'closed': return 'bg-gray-500/20 text-gray-400 border-gray-500/30';
      default: return 'bg-white/10 text-white/60 border-white/20';
    }
  };

  const getStatusIcon = (status) => {
    switch (status) {
      case 'pending': return '⏳';
      case 'in_progress': return '🔄';
      case 'scheduled': return '📅';
      case 'resolved': return '✅';
      case 'closed': return '🔒';
      default: return '❓';
    }
  };

  const getCategoryIcon = (category) => {
    switch (category) {
      case 'benefits': return '🏥';
      case 'payroll': return '💰';
      case 'workplace_issue': return '⚠️';
      case 'general_inquiry': return '💬';
      case 'policy_question': return '📋';
      case 'leave_related': return '🏖️';
      default: return '📝';
    }
  };

  const formatCategory = (category) => {
    return category.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-white/60">Loading HR tickets...</div>
      </div>
    );
  }

  return (
    <div className="flex-1 p-8 overflow-y-auto">
      <div className="max-w-4xl mx-auto space-y-6">
        {/* Header */}
        <div className="glass-card p-6 bg-white/10 border-white/10">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-semibold text-white mb-1">Your HR Tickets</h2>
              <p className="text-sm text-white/60">View your support requests and admin responses</p>
            </div>
            <button
              onClick={fetchTickets}
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
        </div>

        {/* Tickets List */}
        {tickets.length === 0 ? (
          <div className="glass-card p-12 bg-white/10 border-white/10 text-center">
            <div className="text-4xl mb-3">💬</div>
            <p className="text-white/60">No HR tickets yet</p>
            <p className="text-sm text-white/40 mt-2">Go to the Chat tab to create a support request</p>
          </div>
        ) : (
          <div className="space-y-4">
            {tickets.map((ticket) => (
              <div
                key={ticket.id}
                className="glass-card bg-white/10 border-white/10 overflow-hidden"
              >
                {/* Ticket Header */}
                <div
                  className="p-4 cursor-pointer hover:bg-white/5 transition-all"
                  onClick={() => setExpandedTicket(expandedTicket === ticket.id ? null : ticket.id)}
                >
                  <div className="flex items-start justify-between mb-2">
                    <div className="flex items-start gap-3 flex-1">
                      <span className="text-2xl">{getCategoryIcon(ticket.category)}</span>
                      <div className="flex-1 min-w-0">
                        <h3 className="text-white font-semibold mb-1">{ticket.subject}</h3>
                        <p className="text-sm text-white/60">{formatCategory(ticket.category)}</p>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`px-3 py-1 rounded-full text-xs font-medium border whitespace-nowrap ${getStatusColor(ticket.status)}`}>
                        {getStatusIcon(ticket.status)} {ticket.status.replace('_', ' ')}
                      </span>
                      <svg
                        className={`w-5 h-5 text-white/60 transition-transform ${expandedTicket === ticket.id ? 'rotate-180' : ''}`}
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                      </svg>
                    </div>
                  </div>

                  <div className="flex items-center gap-4 text-xs text-white/40 ml-11">
                    <span>Ticket #{ticket.id}</span>
                    {ticket.queue_position && ticket.status === 'pending' && (
                      <span>Queue: #{ticket.queue_position}</span>
                    )}
                    <span>Created: {new Date(ticket.created_at).toLocaleDateString()}</span>
                  </div>
                </div>

                {/* Expanded Details */}
                {expandedTicket === ticket.id && (
                  <div className="border-t border-white/10 p-4 space-y-4 bg-white/5">
                    {/* Description */}
                    {ticket.description && (
                      <div>
                        <p className="text-xs text-white/50 mb-2 font-medium">Description:</p>
                        <p className="text-sm text-white/80 whitespace-pre-wrap">{ticket.description}</p>
                      </div>
                    )}

                    {/* Meeting Details */}
                    {ticket.meeting_type && (
                      <div>
                        <p className="text-xs text-white/50 mb-2 font-medium">Meeting Type:</p>
                        <p className="text-sm text-white/80">{formatCategory(ticket.meeting_type)}</p>
                      </div>
                    )}

                    {/* Preferred Date/Time */}
                    {(ticket.preferred_date || ticket.preferred_time_slot) && (
                      <div>
                        <p className="text-xs text-white/50 mb-2 font-medium">Preferred Schedule:</p>
                        <div className="text-sm text-white/80 space-y-1">
                          {ticket.preferred_date && (
                            <p>📅 {new Date(ticket.preferred_date).toLocaleDateString('en-US', { 
                              weekday: 'long',
                              year: 'numeric', 
                              month: 'long', 
                              day: 'numeric' 
                            })}</p>
                          )}
                          {ticket.preferred_time_slot && (
                            <p>🕐 {formatCategory(ticket.preferred_time_slot)}</p>
                          )}
                        </div>
                      </div>
                    )}

                    {/* Scheduled Meeting */}
                    {ticket.scheduled_date && (
                      <div className="bg-purple-500/10 border border-purple-500/30 rounded-lg p-3">
                        <p className="text-xs text-purple-400 mb-2 font-medium">📅 Scheduled Meeting:</p>
                        <p className="text-sm text-white/90">
                          {new Date(ticket.scheduled_date).toLocaleDateString('en-US', { 
                            weekday: 'long',
                            year: 'numeric', 
                            month: 'long', 
                            day: 'numeric',
                            hour: '2-digit',
                            minute: '2-digit'
                          })}
                        </p>
                      </div>
                    )}

                    {/* Admin Notes - THIS IS KEY! */}
                    {ticket.notes && ticket.notes.length > 0 && (
                      <div className="bg-blue-500/10 border border-blue-500/30 rounded-lg p-3">
                        <p className="text-xs text-blue-400 mb-3 font-medium">💬 Admin Notes:</p>
                        <div className="space-y-3">
                          {ticket.notes.map((note, idx) => (
                            <div key={idx} className="bg-white/5 rounded-lg p-3">
                              <div className="flex items-start justify-between mb-2">
                                <span className="text-xs text-white/50">
                                  {note.admin_email || 'Admin'}
                                </span>
                                <span className="text-xs text-white/40">
                                  {new Date(note.created_at).toLocaleDateString('en-US', {
                                    month: 'short',
                                    day: 'numeric',
                                    hour: '2-digit',
                                    minute: '2-digit'
                                  })}
                                </span>
                              </div>
                              <p className="text-sm text-white/90 whitespace-pre-wrap">{note.note}</p>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Resolution Notes */}
                    {ticket.resolution_notes && (
                      <div className="bg-green-500/10 border border-green-500/30 rounded-lg p-3">
                        <p className="text-xs text-green-400 mb-2 font-medium">✅ Resolution:</p>
                        <p className="text-sm text-white/90 whitespace-pre-wrap">{ticket.resolution_notes}</p>
                      </div>
                    )}

                    {/* Timestamps */}
                    <div className="pt-3 border-t border-white/10 flex items-center justify-between text-xs text-white/40">
                      <span>Last updated: {new Date(ticket.updated_at).toLocaleString()}</span>
                      {ticket.resolved_at && (
                        <span>Resolved: {new Date(ticket.resolved_at).toLocaleDateString()}</span>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default HRTicketsTab;