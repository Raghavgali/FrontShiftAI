import React, { useState, useEffect } from 'react';
import axios from 'axios';
import {
  getAllPTOBalances,
  getAllPTORequests,
  approvePTORequest,
  updatePTOBalance,
  resetPTOBalance,
  resetAllPTOBalances,
  deletePTOBalance,
  getHRTicketQueue,
  pickUpHRTicket,
  scheduleHRMeeting,
  resolveHRTicket,
  addHRTicketNote,
  getHRTicketStats,
  bulkAddUsers
} from '../services/api';
import MonitoringDashboard from './MonitoringDashboard';

const CompanyAdminDashboard = ({ onLogout, userInfo }) => {
  const [activeTab, setActiveTab] = useState('users'); // users, leaves, requests, hr_tickets
  const [users, setUsers] = useState([]);
  const [ptoBalances, setPtoBalances] = useState([]);
  const [ptoRequests, setPtoRequests] = useState([]);
  const [hrTickets, setHrTickets] = useState([]);
  const [hrTicketStats, setHrTicketStats] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [showAddForm, setShowAddForm] = useState(false);
  const [statusFilter, setStatusFilter] = useState('all');
  const [editingBalance, setEditingBalance] = useState(null);

  // HR Ticket filters
  const [hrStatusFilter, setHrStatusFilter] = useState(null);
  const [hrCategoryFilter, setHrCategoryFilter] = useState(null);
  const [hrUrgencyFilter, setHrUrgencyFilter] = useState(null);
  const [hrSortBy, setHrSortBy] = useState('created_at');

  // Selected ticket for modal
  const [selectedTicket, setSelectedTicket] = useState(null);
  const [showScheduleModal, setShowScheduleModal] = useState(false);
  const [scheduleMeetingData, setScheduleMeetingData] = useState({
    datetime: '',
    link: '',
    location: '',
    notes: ''
  });

  // Form state
  const [newUser, setNewUser] = useState({
    email: '',
    password: '',
    name: ''
  });

  // CSV Upload State
  const [csvFileName, setCsvFileName] = useState('');
  const [pendingCsvUsers, setPendingCsvUsers] = useState([]);

  const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  useEffect(() => {
    if (activeTab === 'users') {
      fetchUsers();
    } else if (activeTab === 'leaves') {
      fetchPTOBalances();
    } else if (activeTab === 'requests') {
      fetchPTORequests();
    } else if (activeTab === 'hr_tickets') {
      fetchHRTickets();
      fetchHRTicketStats();
    }
  }, [activeTab, statusFilter, hrStatusFilter, hrCategoryFilter, hrUrgencyFilter, hrSortBy]);

  const fetchUsers = async () => {
    setIsLoading(true);
    try {
      const token = localStorage.getItem('access_token');
      const response = await axios.get(`${API_BASE_URL}/api/admin/company-users`, {
        headers: { Authorization: `Bearer ${token}` }
      });
      setUsers(response.data.users);
    } catch (error) {
      console.error('Error fetching users:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const fetchPTOBalances = async () => {
    setIsLoading(true);
    try {
      const balances = await getAllPTOBalances();
      setPtoBalances(balances);
    } catch (error) {
      console.error('Error fetching PTO balances:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const fetchPTORequests = async () => {
    setIsLoading(true);
    try {
      const filter = statusFilter === 'all' ? null : statusFilter;
      const requests = await getAllPTORequests(filter);
      setPtoRequests(requests);
    } catch (error) {
      console.error('Error fetching PTO requests:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const fetchHRTickets = async () => {
    setIsLoading(true);
    try {
      const filters = {
        status: hrStatusFilter,
        category: hrCategoryFilter,
        urgency: hrUrgencyFilter,
        sortBy: hrSortBy
      };
      const response = await getHRTicketQueue(filters);
      setHrTickets(response.tickets || []);
    } catch (error) {
      console.error('Error fetching HR tickets:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const fetchHRTicketStats = async () => {
    try {
      const stats = await getHRTicketStats();
      setHrTicketStats(stats);
    } catch (error) {
      console.error('Error fetching HR ticket stats:', error);
    }
  };

  const handleAddUser = async (e) => {
    e.preventDefault();
    try {
      const token = localStorage.getItem('access_token');
      await axios.post(
        `${API_BASE_URL}/api/admin/add-user`,
        newUser,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      setShowAddForm(false);
      setNewUser({ email: '', password: '', name: '' });
      fetchUsers();
    } catch (error) {
      alert(error.response?.data?.detail || 'Failed to add user');
    }
  };

  const handleFileSelect = async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = async (event) => {
      try {
        const text = event.target.result;
        const lines = text.split(/\r\n|\n/).filter(line => line.trim());

        if (lines.length < 2) {
          alert('CSV file is empty or missing headers');
          return;
        }

        // Validate headers
        const headers = lines[0].toLowerCase().split(',').map(h => h.trim());
        if (!headers.includes('name') || !headers.includes('email') || !headers.includes('password')) {
          alert('Invalid CSV format. Headers must be: Name, Email, Password');
          return;
        }

        const emailIdx = headers.indexOf('email');
        const nameIdx = headers.indexOf('name');
        const passwordIdx = headers.indexOf('password');

        const usersToAdd = [];
        for (let i = 1; i < lines.length; i++) {
          const values = lines[i].split(',').map(v => v.trim());
          if (values.length >= 3) {
            usersToAdd.push({
              email: values[emailIdx],
              name: values[nameIdx],
              password: values[passwordIdx],
              company: userInfo?.company || '',
              role: 'user'
            });
          }
        }

        if (usersToAdd.length === 0) {
          alert('No valid users found in CSV');
          return;
        }

        setPendingCsvUsers(usersToAdd);
        setCsvFileName(file.name);
        alert(`CSV loaded successfully. ${usersToAdd.length} users ready to populate.`);

      } catch (parseError) {
        console.error(parseError);
        alert('Error parsing CSV file');
      }
    };
    reader.readAsText(file);
    e.target.value = ''; // Reset input to allow re-selection of same file
  };

  const handleBulkSubmit = async () => {
    if (pendingCsvUsers.length === 0) return;

    if (!confirm(`Are you sure you want to add ${pendingCsvUsers.length} users?`)) return;

    setIsLoading(true);
    try {
      const result = await bulkAddUsers(pendingCsvUsers);
      alert(`Bulk add complete!\nAdded: ${result.added}\nFailed: ${result.failed}`);
      setPendingCsvUsers([]);
      setCsvFileName('');
      fetchUsers();
    } catch (err) {
      alert('Bulk add failed: ' + (err.response?.data?.detail || err.message));
    } finally {
      setIsLoading(false);
    }
  };

  const handleDeleteUser = async (email) => {
    if (!confirm(`Delete user: ${email}?`)) return;
    try {
      const token = localStorage.getItem('access_token');
      await axios.delete(`${API_BASE_URL}/api/admin/delete-user`, {
        headers: { Authorization: `Bearer ${token}` },
        data: { email }
      });
      fetchUsers();
    } catch (error) {
      alert(error.response?.data?.detail || 'Failed to delete user');
    }
  };

  const handleUpdatePassword = async (email) => {
    const newPassword = prompt(`Enter new password for ${email}:`);
    if (!newPassword) return;
    try {
      const token = localStorage.getItem('access_token');
      await axios.put(
        `${API_BASE_URL}/api/admin/update-password`,
        { email, new_password: newPassword },
        { headers: { Authorization: `Bearer ${token}` } }
      );
      alert('Password updated successfully');
    } catch (error) {
      alert(error.response?.data?.detail || 'Failed to update password');
    }
  };

  const handleUpdateBalance = async (email, newTotal) => {
    try {
      await updatePTOBalance(email, parseFloat(newTotal));
      setEditingBalance(null);
      fetchPTOBalances();
      alert('Balance updated successfully');
    } catch (error) {
      alert(error.response?.data?.detail || 'Failed to update balance');
    }
  };

  const handleResetBalance = async (email) => {
    if (!confirm(`Reset used and pending days for ${email}?`)) return;
    try {
      await resetPTOBalance(email);
      fetchPTOBalances();
      alert('Balance reset successfully');
    } catch (error) {
      alert(error.response?.data?.detail || 'Failed to reset balance');
    }
  };

  const handleResetAllBalances = async () => {
    if (!confirm('Reset ALL employee balances? This will set used and pending days to 0 for everyone.')) return;
    try {
      const result = await resetAllPTOBalances();
      fetchPTOBalances();
      alert(`Successfully reset balances for ${result.employees_reset} employees`);
    } catch (error) {
      alert(error.response?.data?.detail || 'Failed to reset all balances');
    }
  };

  const handleApproveRequest = async (requestId, status) => {
    const notes = status === 'denied' ? prompt('Reason for denial (optional):') : null;
    try {
      await approvePTORequest(requestId, status, notes);
      fetchPTORequests();
      alert(`Request ${status} successfully`);
    } catch (error) {
      alert(error.response?.data?.detail || `Failed to ${status} request`);
    }
  };

  // HR Ticket Handlers
  const handlePickUpTicket = async (ticketId) => {
    try {
      await pickUpHRTicket(ticketId);
      fetchHRTickets();
      alert('Ticket assigned to you');
    } catch (error) {
      alert(error.response?.data?.detail || 'Failed to pick up ticket');
    }
  };

  const handleOpenScheduleModal = (ticket) => {
    setSelectedTicket(ticket);
    setShowScheduleModal(true);
    setScheduleMeetingData({
      datetime: '',
      link: '',
      location: '',
      notes: ''
    });
  };

  const handleScheduleMeeting = async (e) => {
    e.preventDefault();
    try {
      await scheduleHRMeeting(selectedTicket.id, scheduleMeetingData);
      setShowScheduleModal(false);
      setSelectedTicket(null);
      fetchHRTickets();
      alert('Meeting scheduled successfully');
    } catch (error) {
      alert(error.response?.data?.detail || 'Failed to schedule meeting');
    }
  };

  const handleResolveTicket = async (ticketId, status) => {
    const notes = prompt(`Resolution notes (optional):`);
    try {
      await resolveHRTicket(ticketId, status, notes);
      fetchHRTickets();
      fetchHRTicketStats();
      alert(`Ticket ${status} successfully`);
    } catch (error) {
      alert(error.response?.data?.detail || `Failed to ${status} ticket`);
    }
  };

  const handleAddNote = async (ticketId) => {
    const note = prompt('Add note:');
    if (!note) return;
    try {
      await addHRTicketNote(ticketId, note);
      fetchHRTickets();
      alert('Note added successfully');
    } catch (error) {
      alert(error.response?.data?.detail || 'Failed to add note');
    }
  };

  const getStatusColor = (status) => {
    switch (status) {
      case 'pending': return 'text-yellow-400 bg-yellow-500/20 border-yellow-500/30';
      case 'approved': return 'text-green-400 bg-green-500/20 border-green-500/30';
      case 'denied': return 'text-red-400 bg-red-500/20 border-red-500/30';
      case 'in_progress': return 'text-blue-400 bg-blue-500/20 border-blue-500/30';
      case 'scheduled': return 'text-purple-400 bg-purple-500/20 border-purple-500/30';
      case 'resolved': return 'text-green-400 bg-green-500/20 border-green-500/30';
      case 'closed': return 'text-gray-400 bg-gray-500/20 border-gray-500/30';
      default: return 'text-white/60 bg-white/10 border-white/20';
    }
  };

  const formatCategory = (category) => {
    return category.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
  };

  const getEmailDomain = () => {
    const company = userInfo?.company || '';
    return company.toLowerCase().replace(/[^a-z0-9]/g, '') + '.com';
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-[#0a0a0f] via-[#1a1a24] to-[#0a0a0f] p-8">
      {/* Header */}
      <div className="max-w-7xl mx-auto mb-8">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-white mb-2">Company Admin Dashboard</h1>
            <p className="text-white/60">Manage {userInfo?.company}</p>
          </div>
          <button
            onClick={onLogout}
            className="px-4 py-2 bg-white/10 hover:bg-white/15 border border-white/10 rounded-lg text-white transition-all"
          >
            Logout
          </button>
        </div>
      </div>

      {/* Navigation Tabs */}
      <div className="max-w-7xl mx-auto mb-6">
        <div className="flex space-x-2 bg-white/5 p-1 rounded-lg border border-white/10">
          <button
            onClick={() => setActiveTab('users')}
            className={`flex-1 px-4 py-2 rounded-lg transition-all ${activeTab === 'users'
              ? 'bg-white/10 text-white'
              : 'text-white/60 hover:text-white hover:bg-white/5'
              }`}
          >
            👥 Users
          </button>
          <button
            onClick={() => setActiveTab('leaves')}
            className={`flex-1 px-4 py-2 rounded-lg transition-all ${activeTab === 'leaves'
              ? 'bg-white/10 text-white'
              : 'text-white/60 hover:text-white hover:bg-white/5'
              }`}
          >
            📊 Leave Balances
          </button>
          <button
            onClick={() => setActiveTab('requests')}
            className={`flex-1 px-4 py-2 rounded-lg transition-all ${activeTab === 'requests'
              ? 'bg-white/10 text-white'
              : 'text-white/60 hover:text-white hover:bg-white/5'
              }`}
          >
            📝 Leave Requests
          </button>
          <button
            onClick={() => setActiveTab('hr_tickets')}
            className={`flex-1 px-4 py-2 rounded-lg transition-all ${activeTab === 'hr_tickets'
              ? 'bg-white/10 text-white'
              : 'text-white/60 hover:text-white hover:bg-white/5'
              }`}
          >
            🎫 HR Tickets
          </button>
          <button
            onClick={() => setActiveTab('monitoring')}
            className={`flex-1 px-4 py-2 rounded-lg transition-all ${activeTab === 'monitoring'
              ? 'bg-white/10 text-white'
              : 'text-white/60 hover:text-white hover:bg-white/5'
              }`}
          >
            📈 Monitoring
          </button>
        </div>
      </div>

      {/* Users Tab */}
      {activeTab === 'users' && (
        <div className="max-w-7xl mx-auto">
          <div className="glass-card bg-white/10 p-6">
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-xl font-semibold text-white">Company Users</h2>
              <button
                onClick={() => setShowAddForm(!showAddForm)}
                className="px-4 py-2 bg-white/90 hover:bg-white text-black rounded-lg transition-all"
              >
                {showAddForm ? 'Cancel' : '+ Add User'}
              </button>
              <button
                onClick={() => document.getElementById('csvUpload').click()}
                className="ml-2 px-4 py-2 bg-blue-500/80 hover:bg-blue-500 text-white rounded-lg transition-all"
              >
                Upload CSV
              </button>
            </div>

            {showAddForm && (
              <form onSubmit={handleAddUser} className="mb-6 p-4 bg-white/5 rounded-lg border border-white/10">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-white/60 text-sm mb-2">Email</label>
                    <input
                      type="email"
                      placeholder={`user@${getEmailDomain()}`}
                      value={newUser.email}
                      onChange={(e) => setNewUser({ ...newUser, email: e.target.value })}
                      required
                      className="w-full px-4 py-2 bg-white/10 border border-white/10 rounded-lg text-white placeholder-white/40"
                    />
                  </div>
                  <div>
                    <label className="block text-white/60 text-sm mb-2">Password</label>
                    <input
                      type="password"
                      placeholder="Password"
                      value={newUser.password}
                      onChange={(e) => setNewUser({ ...newUser, password: e.target.value })}
                      required
                      className="w-full px-4 py-2 bg-white/10 border border-white/10 rounded-lg text-white placeholder-white/40"
                    />
                  </div>
                  <div className="col-span-2">
                    <label className="block text-white/60 text-sm mb-2">Full Name</label>
                    <input
                      type="text"
                      placeholder="John Doe"
                      value={newUser.name}
                      onChange={(e) => setNewUser({ ...newUser, name: e.target.value })}
                      required
                      className="w-full px-4 py-2 bg-white/10 border border-white/10 rounded-lg text-white placeholder-white/40"
                    />
                  </div>
                </div>
                <button
                  type="submit"
                  className="mt-4 px-6 py-2 bg-white/90 hover:bg-white text-black rounded-lg transition-all"
                >
                  Add User
                </button>
              </form>
            )}

            {isLoading ? (
              <p className="text-white/60 text-center py-8">Loading...</p>
            ) : users.length === 0 ? (
              <p className="text-white/60 text-center py-8">No users yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-white/10">
                      <th className="text-left py-3 px-4 text-white/80 font-medium">Name</th>
                      <th className="text-left py-3 px-4 text-white/80 font-medium">Email</th>
                      <th className="text-left py-3 px-4 text-white/80 font-medium">Password</th>
                      <th className="text-right py-3 px-4 text-white/80 font-medium">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {users.map(user => (
                      <tr key={user.email} className="border-b border-white/5 hover:bg-white/5">
                        <td className="py-3 px-4 text-white">{user.name}</td>
                        <td className="py-3 px-4 text-white/70">{user.email}</td>
                        <td className="py-3 px-4 text-white/50 font-mono text-sm">{user.password}</td>
                        <td className="py-3 px-4 text-right">
                          <button
                            onClick={() => handleUpdatePassword(user.email)}
                            className="px-3 py-1 bg-blue-500/20 hover:bg-blue-500/30 border border-blue-500/30 rounded text-blue-300 text-sm transition-all mr-2"
                          >
                            Change Password
                          </button>
                          <button
                            onClick={() => handleDeleteUser(user.email)}
                            className="px-3 py-1 bg-red-500/20 hover:bg-red-500/30 border border-red-500/30 rounded text-red-300 text-sm transition-all"
                          >
                            Delete
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* Hidden File Input */}
            <input
              type="file"
              id="csvUpload"
              accept=".csv"
              style={{ display: 'none' }}
              onChange={async (e) => {
                const file = e.target.files[0];
                if (!file) return;

                const reader = new FileReader();
                reader.onload = async (event) => {
                  try {
                    const text = event.target.result;
                    const lines = text.split(/\r\n|\n/).filter(line => line.trim());

                    if (lines.length < 2) {
                      alert('CSV file is empty or missing headers');
                      return;
                    }

                    // Validate headers
                    const headers = lines[0].toLowerCase().split(',').map(h => h.trim());
                    if (!headers.includes('name') || !headers.includes('email') || !headers.includes('password')) {
                      alert('Invalid CSV format. Headers must be: Name, Email, Password');
                      return;
                    }

                    const emailIdx = headers.indexOf('email');
                    const nameIdx = headers.indexOf('name');
                    const passwordIdx = headers.indexOf('password');

                    const usersToAdd = [];
                    for (let i = 1; i < lines.length; i++) {
                      const values = lines[i].split(',').map(v => v.trim());
                      if (values.length >= 3) {
                        // Simple parsing assuming no commas in fields for now
                        usersToAdd.push({
                          email: values[emailIdx],
                          name: values[nameIdx],
                          password: values[passwordIdx],
                          company: userInfo?.company || '', // will be enforced by backend mostly
                          role: 'user'
                        });
                      }
                    }

                    if (usersToAdd.length === 0) {
                      alert('No valid users found in CSV');
                      return;
                    }

                    if (confirm(`Attempting to add ${usersToAdd.length} users. Continue?`)) {
                      setIsLoading(true);
                      try {
                        const result = await bulkAddUsers(usersToAdd);
                        alert(`Bulk add complete!\nAdded: ${result.added}\nFailed: ${result.failed}`);
                        fetchUsers();
                      } catch (err) {
                        alert('Bulk add failed: ' + (err.response?.data?.detail || err.message));
                      } finally {
                        setIsLoading(false);
                        e.target.value = ''; // Reset input
                      }
                    }
                  } catch (parseError) {
                    console.error(parseError);
                    alert('Error parsing CSV file');
                  }
                };
                reader.readAsText(file);
              }}
            />
          </div>
        </div>
      )}

      {/* Leave Balances Tab */}
      {
        activeTab === 'leaves' && (
          <div className="max-w-7xl mx-auto">
            <div className="glass-card bg-white/10 p-6">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-xl font-semibold text-white">Employee Leave Balances</h2>
                <button
                  onClick={handleResetAllBalances}
                  className="px-4 py-2 bg-red-500/20 hover:bg-red-500/30 border border-red-500/30 text-red-300 rounded-lg transition-all"
                >
                  Reset All Balances
                </button>
              </div>

              {isLoading ? (
                <p className="text-white/60 text-center py-8">Loading...</p>
              ) : ptoBalances.length === 0 ? (
                <p className="text-white/60 text-center py-8">No leave balances found.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="border-b border-white/10">
                        <th className="text-left py-3 px-4 text-white/80 font-medium">Employee</th>
                        <th className="text-center py-3 px-4 text-white/80 font-medium">Total</th>
                        <th className="text-center py-3 px-4 text-white/80 font-medium">Used</th>
                        <th className="text-center py-3 px-4 text-white/80 font-medium">Pending</th>
                        <th className="text-center py-3 px-4 text-white/80 font-medium">Available</th>
                        <th className="text-right py-3 px-4 text-white/80 font-medium">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {ptoBalances.map(balance => (
                        <tr key={balance.email} className="border-b border-white/5 hover:bg-white/5">
                          <td className="py-3 px-4 text-white">{balance.email}</td>
                          <td className="py-3 px-4 text-center">
                            {editingBalance === balance.email ? (
                              <input
                                type="number"
                                step="0.5"
                                defaultValue={balance.total_days}
                                onBlur={(e) => handleUpdateBalance(balance.email, e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter') handleUpdateBalance(balance.email, e.target.value);
                                  if (e.key === 'Escape') setEditingBalance(null);
                                }}
                                autoFocus
                                className="w-20 px-2 py-1 bg-white/10 border border-white/20 rounded text-white text-center"
                              />
                            ) : (
                              <button
                                onClick={() => setEditingBalance(balance.email)}
                                className="text-white/90 hover:text-white"
                              >
                                {balance.total_days} days
                              </button>
                            )}
                          </td>
                          <td className="py-3 px-4 text-center text-white/70">{balance.used_days} days</td>
                          <td className="py-3 px-4 text-center text-yellow-400">{balance.pending_days} days</td>
                          <td className="py-3 px-4 text-center text-green-400 font-semibold">{balance.remaining_days} days</td>
                          <td className="py-3 px-4 text-right">
                            <button
                              onClick={() => handleResetBalance(balance.email)}
                              className="px-3 py-1 bg-orange-500/20 hover:bg-orange-500/30 border border-orange-500/30 rounded text-orange-300 text-sm transition-all"
                            >
                              Reset
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )
      }

      {/* Leave Requests Tab */}
      {activeTab === 'requests' && (
        <div className="max-w-7xl mx-auto">
          <div className="glass-card bg-white/10 p-6">
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-xl font-semibold text-white">Leave Requests</h2>
              <div className="flex space-x-2">
                {['all', 'pending', 'approved', 'denied'].map(filter => (
                  <button
                    key={filter}
                    onClick={() => setStatusFilter(filter)}
                    className={`px-3 py-1 rounded-lg text-sm transition-all ${statusFilter === filter
                      ? 'bg-white/20 text-white'
                      : 'bg-white/5 text-white/60 hover:text-white hover:bg-white/10'
                      }`}
                  >
                    {filter.charAt(0).toUpperCase() + filter.slice(1)}
                  </button>
                ))}
              </div>

              </div>

            {isLoading ? (
              <p className="text-white/60 text-center py-8">Loading...</p>
            ) : ptoRequests.length === 0 ? (
              <p className="text-white/60 text-center py-8">No leave requests found.</p>
            ) : (
              <div className="space-y-4">
                {ptoRequests.map(request => (
                  <div
                    key={request.id}
                    className="p-4 bg-white/5 rounded-lg border border-white/10 hover:bg-white/8 transition-all"
                  >
                    <div className="flex items-start justify-between">
                      <div className="flex-1">
                        <div className="flex items-center space-x-3 mb-2">
                          <h3 className="text-lg font-medium text-white">{request.email}</h3>
                          <span className={`px-2 py-1 rounded text-xs border ${getStatusColor(request.status)}`}>
                            {request.status}
                          </span>
                        </div>
                        <div className="grid grid-cols-2 gap-4 text-sm">
                          <div>
                            <p className="text-white/50 mb-1">Dates</p>
                            <p className="text-white/90">
                              {new Date(request.start_date).toLocaleDateString()} - {new Date(request.end_date).toLocaleDateString()}
                            </p>
                          </div>
                          <div>
                            <p className="text-white/50 mb-1">Days Requested</p>
                            <p className="text-white/90">{request.days_requested} days</p>
                          </div>
                          {request.reason && (
                            <div className="col-span-2">
                              <p className="text-white/50 mb-1">Reason</p>
                              <p className="text-white/70">{request.reason}</p>
                            </div>
                          )}
                          {request.admin_notes && (
                            <div className="col-span-2">
                              <p className="text-white/50 mb-1">Admin Notes</p>
                              <p className="text-white/70">{request.admin_notes}</p>
                            </div>
                          )}
                        </div>
                      </div>
                      {request.status === 'pending' && (
                        <div className="flex space-x-2 ml-4">
                          <button
                            onClick={() => handleApproveRequest(request.id, 'approved')}
                            className="px-4 py-2 bg-green-500/20 hover:bg-green-500/30 border border-green-500/30 rounded text-green-300 transition-all"
                          >
                            ✓ Approve
                          </button>
                          <button
                            onClick={() => handleApproveRequest(request.id, 'denied')}
                            className="px-4 py-2 bg-red-500/20 hover:bg-red-500/30 border border-red-500/30 rounded text-red-300 transition-all"
                          >
                            ✗ Deny
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* HR Tickets Tab */}
      {
        activeTab === 'hr_tickets' && (
          <div className="max-w-7xl mx-auto">
            {/* Stats Cards */}
            {hrTicketStats && (
              <div className="grid grid-cols-5 gap-4 mb-6">
                <div className="glass-card bg-white/10 p-4">
                  <p className="text-white/60 text-sm mb-1">Pending</p>
                  <p className="text-2xl font-bold text-yellow-400">{hrTicketStats.total_pending}</p>
                </div>
                <div className="glass-card bg-white/10 p-4">
                  <p className="text-white/60 text-sm mb-1">In Progress</p>
                  <p className="text-2xl font-bold text-blue-400">{hrTicketStats.total_in_progress}</p>
                </div>
                <div className="glass-card bg-white/10 p-4">
                  <p className="text-white/60 text-sm mb-1">Scheduled</p>
                  <p className="text-2xl font-bold text-purple-400">{hrTicketStats.total_scheduled}</p>
                </div>
                <div className="glass-card bg-white/10 p-4">
                  <p className="text-white/60 text-sm mb-1">Resolved Today</p>
                  <p className="text-2xl font-bold text-green-400">{hrTicketStats.total_resolved_today}</p>
                </div>
                <div className="glass-card bg-white/10 p-4">
                  <p className="text-white/60 text-sm mb-1">Avg Resolution</p>
                  <p className="text-2xl font-bold text-white/90">
                    {hrTicketStats.average_resolution_time_hours ?
                      `${hrTicketStats.average_resolution_time_hours.toFixed(1)}h` :
                      'N/A'
                    }
                  </p>
                </div>
              </div>
            )}

            <div className="glass-card bg-white/10 p-6">
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-xl font-semibold text-white">HR Ticket Queue</h2>
                <div className="flex space-x-2">
                  {/* Status Filter */}
                  <select
                    value={hrStatusFilter || ''}
                    onChange={(e) => setHrStatusFilter(e.target.value || null)}
                    className="px-3 py-1 bg-white/10 border border-white/10 rounded-lg text-white text-sm"
                  >
                    <option value="">All Status</option>
                    <option value="pending">Pending</option>
                    <option value="in_progress">In Progress</option>
                    <option value="scheduled">Scheduled</option>
                    <option value="resolved">Resolved</option>
                    <option value="closed">Closed</option>
                  </select>

                  {/* Category Filter */}
                  <select
                    value={hrCategoryFilter || ''}
                    onChange={(e) => setHrCategoryFilter(e.target.value || null)}
                    className="px-3 py-1 bg-white/10 border border-white/10 rounded-lg text-white text-sm"
                  >
                    <option value="">All Categories</option>
                    <option value="benefits">Benefits</option>
                    <option value="payroll">Payroll</option>
                    <option value="workplace_issue">Workplace Issue</option>
                    <option value="general_inquiry">General Inquiry</option>
                    <option value="policy_question">Policy Question</option>
                    <option value="leave_related">Leave Related</option>
                    <option value="other">Other</option>
                  </select>

                  {/* Urgency Filter */}
                  <select
                    value={hrUrgencyFilter || ''}
                    onChange={(e) => setHrUrgencyFilter(e.target.value || null)}
                    className="px-3 py-1 bg-white/10 border border-white/10 rounded-lg text-white text-sm"
                  >
                    <option value="">All Urgency</option>
                    <option value="normal">Normal</option>
                    <option value="urgent">Urgent</option>
                  </select>

                  {/* Sort By */}
                  <select
                    value={hrSortBy}
                    onChange={(e) => setHrSortBy(e.target.value)}
                    className="px-3 py-1 bg-white/10 border border-white/10 rounded-lg text-white text-sm"
                  >
                    <option value="created_at">Sort: Date</option>
                    <option value="urgency">Sort: Urgency</option>
                    <option value="category">Sort: Category</option>
                  </select>
                </div>
              </div>

              {isLoading ? (
                <p className="text-white/60 text-center py-8">Loading...</p>
              ) : hrTickets.length === 0 ? (
                <p className="text-white/60 text-center py-8">No tickets found.</p>
              ) : (
                <div className="space-y-4">
                  {hrTickets.map(ticket => (
                    <div
                      key={ticket.id}
                      className="p-4 bg-white/5 rounded-lg border border-white/10 hover:bg-white/8 transition-all"
                    >
                      <div className="flex items-start justify-between">
                        <div className="flex-1">
                          <div className="flex items-center space-x-3 mb-2">
                            <h3 className="text-lg font-medium text-white">{ticket.subject}</h3>
                            <span className={`px-2 py-1 rounded text-xs border ${getStatusColor(ticket.status)}`}>
                              {ticket.status.replace('_', ' ')}
                            </span>
                            {ticket.urgency === 'urgent' && (
                              <span className="px-2 py-1 rounded text-xs border border-red-500/30 bg-red-500/20 text-red-400">
                                🚨 URGENT
                              </span>
                            )}
                          </div>
                          <div className="grid grid-cols-2 gap-4 text-sm mb-3">
                            <div>
                              <p className="text-white/50 mb-1">Employee</p>
                              <p className="text-white/90">{ticket.email}</p>
                            </div>
                            <div>
                              <p className="text-white/50 mb-1">Category</p>
                              <p className="text-white/90">{formatCategory(ticket.category)}</p>
                            </div>
                            <div>
                              <p className="text-white/50 mb-1">Meeting Type</p>
                              <p className="text-white/90">{formatCategory(ticket.meeting_type)}</p>
                            </div>
                            <div>
                              <p className="text-white/50 mb-1">Created</p>
                              <p className="text-white/90">{new Date(ticket.created_at).toLocaleDateString()}</p>
                            </div>
                            {ticket.queue_position && ticket.status === 'pending' && (
                              <div>
                                <p className="text-white/50 mb-1">Queue Position</p>
                                <p className="text-white/90">#{ticket.queue_position}</p>
                              </div>
                            )}
                            {ticket.preferred_date && (
                              <div>
                                <p className="text-white/50 mb-1">Preferred Date</p>
                                <p className="text-white/90">
                                  {new Date(ticket.preferred_date).toLocaleDateString()}
                                  {ticket.preferred_time_slot && ` (${ticket.preferred_time_slot})`}
                                </p>
                              </div>
                            )}
                          </div>
                          <div className="text-sm">
                            <p className="text-white/50 mb-1">Description</p>
                            <p className="text-white/70">{ticket.description}</p>
                          </div>
                          {ticket.admin_notes && (
                            <div className="mt-3 text-sm">
                              <p className="text-white/50 mb-1">Admin Notes</p>
                              <p className="text-white/70 whitespace-pre-wrap">{ticket.admin_notes}</p>
                            </div>
                          )}
                          {ticket.scheduled_datetime && (
                            <div className="mt-3 p-3 bg-purple-500/10 border border-purple-500/30 rounded">
                              <p className="text-purple-300 text-sm font-medium mb-2">📅 Meeting Scheduled</p>
                              <div className="grid grid-cols-2 gap-2 text-xs">
                                <div>
                                  <p className="text-white/50">Date & Time</p>
                                  <p className="text-white/90">{new Date(ticket.scheduled_datetime).toLocaleString()}</p>
                                </div>
                                {ticket.meeting_link && (
                                  <div>
                                    <p className="text-white/50">Meeting Link</p>
                                    <a href={ticket.meeting_link} target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:underline">
                                      Join Meeting
                                    </a>
                                  </div>
                                )}
                                {ticket.meeting_location && (
                                  <div>
                                    <p className="text-white/50">Location</p>
                                    <p className="text-white/90">{ticket.meeting_location}</p>
                                  </div>
                                )}
                              </div>
                            </div>
                          )}
                        </div>
                        <div className="flex flex-col space-y-2 ml-4">
                          {ticket.status === 'pending' && (
                            <button
                              onClick={() => handlePickUpTicket(ticket.id)}
                              className="px-4 py-2 bg-blue-500/20 hover:bg-blue-500/30 border border-blue-500/30 rounded text-blue-300 transition-all text-sm"
                            >
                              Pick Up
                            </button>
                          )}
                          {(ticket.status === 'in_progress' || ticket.status === 'pending') && (
                            <button
                              onClick={() => handleOpenScheduleModal(ticket)}
                              className="px-4 py-2 bg-purple-500/20 hover:bg-purple-500/30 border border-purple-500/30 rounded text-purple-300 transition-all text-sm"
                            >
                              Schedule Meeting
                            </button>
                          )}
                          {(ticket.status === 'in_progress' || ticket.status === 'scheduled') && (
                            <>
                              <button
                                onClick={() => handleResolveTicket(ticket.id, 'resolved')}
                                className="px-4 py-2 bg-green-500/20 hover:bg-green-500/30 border border-green-500/30 rounded text-green-300 transition-all text-sm"
                              >
                                ✓ Resolve
                              </button>
                              <button
                                onClick={() => handleResolveTicket(ticket.id, 'closed')}
                                className="px-4 py-2 bg-gray-500/20 hover:bg-gray-500/30 border border-gray-500/30 rounded text-gray-300 transition-all text-sm"
                              >
                                Close
                              </button>
                            </>
                          )}
                          {ticket.status !== 'resolved' && ticket.status !== 'closed' && (
                            <button
                              onClick={() => handleAddNote(ticket.id)}
                              className="px-4 py-2 bg-white/10 hover:bg-white/15 border border-white/10 rounded text-white transition-all text-sm"
                            >
                              Add Note
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )
      }

      {/* Schedule Meeting Modal */}
      {showScheduleModal && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50">
          <div className="glass-card bg-white/10 p-6 w-full max-w-md">
            <h3 className="text-xl font-semibold text-white mb-4">Schedule Meeting</h3>
            <form onSubmit={handleScheduleMeeting} className="space-y-4">
              <div>
                <label className="block text-white/60 text-sm mb-2">Date & Time *</label>
                <input
                  type="datetime-local"
                  value={scheduleMeetingData.datetime}
                  onChange={(e) => setScheduleMeetingData({ ...scheduleMeetingData, datetime: e.target.value })}
                  required
                  className="w-full px-4 py-2 bg-white/10 border border-white/10 rounded-lg text-white"
                />
              </div>
              <div>
                <label className="block text-white/60 text-sm mb-2">Meeting Link (for online meetings)</label>
                <input
                  type="url"
                  placeholder="https://meet.google.com/..."
                  value={scheduleMeetingData.link}
                  onChange={(e) => setScheduleMeetingData({ ...scheduleMeetingData, link: e.target.value })}
                  className="w-full px-4 py-2 bg-white/10 border border-white/10 rounded-lg text-white placeholder-white/40"
                />
              </div>
              <div>
                <label className="block text-white/60 text-sm mb-2">Location (for in-person meetings)</label>
                <input
                  type="text"
                  placeholder="HR Office, Room 203"
                  value={scheduleMeetingData.location}
                  onChange={(e) => setScheduleMeetingData({ ...scheduleMeetingData, location: e.target.value })}
                  className="w-full px-4 py-2 bg-white/10 border border-white/10 rounded-lg text-white placeholder-white/40"
                />
              </div>
              <div>
                <label className="block text-white/60 text-sm mb-2">Notes</label>
                <textarea
                  placeholder="Looking forward to our meeting..."
                  value={scheduleMeetingData.notes}
                  onChange={(e) => setScheduleMeetingData({ ...scheduleMeetingData, notes: e.target.value })}
                  rows="3"
                  className="w-full px-4 py-2 bg-white/10 border border-white/10 rounded-lg text-white placeholder-white/40"
                />
              </div>
              <div className="flex space-x-3">
                <button
                  type="submit"
                  className="flex-1 px-4 py-2 bg-white/90 hover:bg-white text-black rounded-lg transition-all"
                >
                  Schedule
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setShowScheduleModal(false);
                    setSelectedTicket(null);
                  }}
                  className="flex-1 px-4 py-2 bg-white/10 hover:bg-white/15 border border-white/10 rounded-lg text-white transition-all"
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
      {/* Monitoring Tab */}
      {activeTab === 'monitoring' && (
        <div className="max-w-7xl mx-auto">
          <div className="glass-card bg-white/10 overflow-hidden rounded-xl">
            <MonitoringDashboard userRole="company_admin" />
          </div>
        </div>
      )}
    </div>
  );
};

export default CompanyAdminDashboard;