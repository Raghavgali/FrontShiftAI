import React, { useState, useEffect } from 'react';
import axios from 'axios';
import MonitoringDashboard from './MonitoringDashboard';

const SuperAdminDashboard = ({ onLogout, userInfo }) => {
  const [activeTab, setActiveTab] = useState('companies'); // 'companies' or 'admins'
  const [companyAdmins, setCompanyAdmins] = useState([]);
  const [companies, setCompanies] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [showAddAdminForm, setShowAddAdminForm] = useState(false);
  const [showAddCompanyForm, setShowAddCompanyForm] = useState(false);

  // Admin form state
  const [newAdmin, setNewAdmin] = useState({
    email: '',
    password: '',
    name: '',
    company: ''
  });

  // Company form state
  const [newCompany, setNewCompany] = useState({
    company_name: '',
    domain: '',
    email_domain: '', // Added
    url: ''
  });

  // Password change state
  const [passwordChangeData, setPasswordChangeData] = useState({
    email: null,
    newPassword: ''
  });

  // Task tracking state
  const [processingTask, setProcessingTask] = useState(null);
  const [taskStatus, setTaskStatus] = useState(null);

  const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  useEffect(() => {
    fetchData();
  }, []);

  // Poll task status when we have an active task
  useEffect(() => {
    if (processingTask) {
      const interval = setInterval(async () => {
        try {
          const token = localStorage.getItem('access_token');
          const response = await axios.get(
            `${API_BASE_URL}/api/admin/company-task-status/${processingTask}`,
            { headers: { Authorization: `Bearer ${token}` } }
          );

          setTaskStatus(response.data);

          // Stop polling if completed or failed
          if (response.data.status === 'completed' || response.data.status === 'failed') {
            clearInterval(interval);
            // Refresh data after completion
            if (response.data.status === 'completed') {
              setTimeout(() => {
                fetchData();
                setProcessingTask(null);
                setTaskStatus(null);
              }, 2000);
            }
          }
        } catch (error) {
          console.error('Error checking task status:', error);
          clearInterval(interval);
        }
      }, 2000); // Poll every 2 seconds

      return () => clearInterval(interval);
    }
  }, [processingTask]);

  const fetchData = async () => {
    setIsLoading(true);
    try {
      const token = localStorage.getItem('access_token');

      const [adminsRes, companiesRes] = await Promise.all([
        axios.get(`${API_BASE_URL}/api/admin/company-admins`, {
          headers: { Authorization: `Bearer ${token}` }
        }),
        axios.get(`${API_BASE_URL}/api/admin/all-companies`, {
          headers: { Authorization: `Bearer ${token}` }
        })
      ]);

      setCompanyAdmins(adminsRes.data.admins);
      setCompanies(companiesRes.data.companies);
    } catch (error) {
      console.error('Error fetching data:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const handleAddAdmin = async (e) => {
    e.preventDefault();

    try {
      const token = localStorage.getItem('access_token');
      await axios.post(
        `${API_BASE_URL}/api/admin/add-company-admin`,
        newAdmin,
        { headers: { Authorization: `Bearer ${token}` } }
      );

      setShowAddAdminForm(false);
      setNewAdmin({ email: '', password: '', name: '', company: '' });
      fetchData();
    } catch (error) {
      alert(error.response?.data?.detail || 'Failed to add admin');
    }
  };

  const handleAddCompany = async (e) => {
    e.preventDefault();

    try {
      const token = localStorage.getItem('access_token');
      const response = await axios.post(
        `${API_BASE_URL}/api/admin/add-company`,
        newCompany,
        { headers: { Authorization: `Bearer ${token}` } }
      );

      // Start tracking the task
      setProcessingTask(response.data.task_id);
      setTaskStatus({
        status: 'pending',
        message: 'Task queued for processing'
      });

      setShowAddCompanyForm(false);
      setShowAddCompanyForm(false);
      setNewCompany({ company_name: '', domain: '', email_domain: '', url: '' });
    } catch (error) {
      alert(error.response?.data?.detail || 'Failed to add company');
    }
  };

  const handleDeleteCompany = async (companyName) => {
    if (!confirm(`Delete company "${companyName}"? This will delete all associated data and rebuild the index. This action is irreversible.`)) return;

    try {
      const token = localStorage.getItem('access_token');
      const response = await axios.delete(
        `${API_BASE_URL}/api/company/delete`,
        {
          headers: { Authorization: `Bearer ${token}` },
          params: { company_name: companyName } // Sent as query param
        }
      );

      // Start tracking the deletion task
      setProcessingTask(response.data.task_id);
      setTaskStatus({
        status: 'pending',
        message: 'Deletion task queued'
      });

    } catch (error) {
      alert(error.response?.data?.detail || 'Failed to delete company');
    }
  };

  const handleBulkDeleteUsers = async (companyName) => {
    if (!confirm(`Delete ALL users for company "${companyName}"? This cannot be undone.`)) return;

    try {
      const token = localStorage.getItem('access_token');
      const response = await axios.delete(
        `${API_BASE_URL}/api/admin/bulk-delete-users`,
        {
          headers: { Authorization: `Bearer ${token}` },
          params: { company_name: companyName }
        }
      );

      alert(response.data.message);
      fetchData(); // Refresh counts
    } catch (error) {
      alert(error.response?.data?.detail || 'Failed to delete users');
    }
  };

  const handleDeleteAdmin = async (email) => {
    if (!confirm(`Delete admin: ${email}?`)) return;

    try {
      const token = localStorage.getItem('access_token');
      await axios.delete(`${API_BASE_URL}/api/admin/delete-company-admin`, {
        headers: { Authorization: `Bearer ${token}` },
        data: { email }
      });

      fetchData();
    } catch (error) {
      alert(error.response?.data?.detail || 'Failed to delete admin');
    }
  };

  const handleChangePassword = async (e) => {
    e.preventDefault();
    try {
      const token = localStorage.getItem('access_token');
      await axios.put(
        `${API_BASE_URL}/api/admin/update-password`,
        {
          email: passwordChangeData.email,
          new_password: passwordChangeData.newPassword
        },
        { headers: { Authorization: `Bearer ${token}` } }
      );

      alert(`Password updated for ${passwordChangeData.email}`);
      setPasswordChangeData({ email: null, newPassword: '' });
    } catch (error) {
      alert(error.response?.data?.detail || 'Failed to update password');
    }
  };

  const getStatusColor = (status) => {
    switch (status) {
      case 'pending': return 'text-yellow-400';
      case 'running': return 'text-blue-400';
      case 'completed': return 'text-green-400';
      case 'failed': return 'text-red-400';
      default: return 'text-white/60';
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-[#0a0a0f] via-[#1a1a24] to-[#0a0a0f] p-8">
      {/* Header */}
      <div className="max-w-7xl mx-auto mb-8">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-white mb-2">Super Admin Dashboard</h1>
            <p className="text-white/60">Manage companies and admins across all organizations</p>
          </div>
          <button
            onClick={onLogout}
            className="px-4 py-2 bg-white/10 hover:bg-white/15 border border-white/10 rounded-lg text-white transition-all"
          >
            Logout
          </button>
        </div>
      </div>

      {/* Processing Status Banner */}
      {taskStatus && (
        <div className="max-w-7xl mx-auto mb-6">
          <div className={`glass-card bg-white/10 p-4 border-l-4 ${taskStatus.status === 'completed' ? 'border-green-500' :
            taskStatus.status === 'failed' ? 'border-red-500' :
              taskStatus.status === 'running' ? 'border-blue-500' :
                'border-yellow-500'
            }`}>
            <div className="flex items-center justify-between">
              <div>
                <p className={`font-semibold ${getStatusColor(taskStatus.status)}`}>
                  Status: {taskStatus.status.toUpperCase()}
                </p>
                <p className="text-white/70 text-sm mt-1">{taskStatus.message}</p>
                {taskStatus.error && (
                  <p className="text-red-400 text-sm mt-1">Error: {taskStatus.error}</p>
                )}
              </div>
              {taskStatus.status === 'running' && (
                <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-white"></div>
              )}
              {taskStatus.status === 'completed' && (
                <svg className="w-6 h-6 text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Stats */}
      <div className="max-w-7xl mx-auto grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        <div className="glass-card bg-white/10 p-6">
          <p className="text-white/60 text-sm mb-2">Total Companies</p>
          <p className="text-3xl font-bold text-white">{companies.length}</p>
        </div>
        <div className="glass-card bg-white/10 p-6">
          <p className="text-white/60 text-sm mb-2">Company Admins</p>
          <p className="text-3xl font-bold text-white">{companyAdmins.length}</p>
        </div>
        <div className="glass-card bg-white/10 p-6">
          <p className="text-white/60 text-sm mb-2">Your Role</p>
          <p className="text-lg font-semibold text-white">Super Administrator</p>
        </div>
      </div>

      {/* Tab Navigation */}
      <div className="max-w-7xl mx-auto mb-6">
        <div className="glass-card bg-white/10 p-2 inline-flex rounded-lg">
          <button
            onClick={() => setActiveTab('companies')}
            className={`px-6 py-2 rounded-lg transition-all ${activeTab === 'companies'
              ? 'bg-white text-black font-semibold'
              : 'text-white/70 hover:text-white hover:bg-white/10'
              }`}
          >
            Companies
          </button>
          <button
            onClick={() => setActiveTab('admins')}
            className={`px-6 py-2 rounded-lg transition-all ${activeTab === 'admins'
              ? 'bg-white text-black font-semibold'
              : 'text-white/70 hover:text-white hover:bg-white/10'
              }`}
          >
            Company Administrators
          </button>
          <button
            onClick={() => setActiveTab('monitoring')}
            className={`px-6 py-2 rounded-lg transition-all ${activeTab === 'monitoring'
              ? 'bg-white text-black font-semibold'
              : 'text-white/70 hover:text-white hover:bg-white/10'
              }`}
          >
            Monitoring
          </button>
        </div>
      </div>

      {/* Companies Tab */}
      {activeTab === 'companies' && (
        <div className="max-w-7xl mx-auto">
          <div className="glass-card bg-white/10 p-6">
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-xl font-semibold text-white">Companies</h2>
              <button
                onClick={() => setShowAddCompanyForm(!showAddCompanyForm)}
                className="px-4 py-2 bg-white/90 hover:bg-white text-black rounded-lg transition-all"
              >
                {showAddCompanyForm ? 'Cancel' : '+ Add Company'}
              </button>
            </div>

            {/* Add Company Form */}
            {showAddCompanyForm && (
              <form onSubmit={handleAddCompany} className="mb-6 p-4 bg-white/5 rounded-lg border border-white/10">
                <div className="grid grid-cols-1 gap-4">
                  <input
                    type="text"
                    placeholder="Company Name"
                    value={newCompany.company_name}
                    onChange={(e) => setNewCompany({ ...newCompany, company_name: e.target.value })}
                    required
                    className="px-4 py-2 bg-white/10 border border-white/10 rounded-lg text-white placeholder-white/40"
                  />
                  <select
                    value={newCompany.domain}
                    onChange={(e) => setNewCompany({ ...newCompany, domain: e.target.value })}
                    required
                    className="px-4 py-2 bg-white/10 border border-white/10 rounded-lg text-white"
                  >
                    <option value="">Select Domain</option>
                    <option value="Healthcare">Healthcare</option>
                    <option value="Retail">Retail</option>
                    <option value="Manufacturing">Manufacturing</option>
                    <option value="Construction">Construction</option>
                    <option value="Hospitality">Hospitality</option>
                    <option value="Finance">Finance</option>
                    <option value="Cleaning&Maintenance">Cleaning & Maintenance</option>
                    <option value="Logistics">Logistics</option>
                    <option value="FieldServiceTechnicians">Field Service Technicians</option>
                  </select>
                  <input
                    type="text"
                    placeholder="Email Domain (e.g., gmail.com)"
                    value={newCompany.email_domain}
                    onChange={(e) => setNewCompany({ ...newCompany, email_domain: e.target.value })}
                    required
                    className="px-4 py-2 bg-white/10 border border-white/10 rounded-lg text-white placeholder-white/40"
                  />
                  <input
                    type="url"
                    placeholder="PDF URL (e.g., https://example.com/handbook.pdf)"
                    value={newCompany.url}
                    onChange={(e) => setNewCompany({ ...newCompany, url: e.target.value })}
                    required
                    className="px-4 py-2 bg-white/10 border border-white/10 rounded-lg text-white placeholder-white/40"
                  />
                </div>
                <button
                  type="submit"
                  className="mt-4 px-6 py-2 bg-white/90 hover:bg-white text-black rounded-lg transition-all"
                >
                  Add Company
                </button>
                <p className="mt-2 text-xs text-white/50">
                  Note: This will process the PDF and sync to Google Cloud Storage. This may take a few minutes.
                </p>
              </form>
            )}

            {/* Companies Table */}
            {isLoading ? (
              <p className="text-white/60 text-center py-8">Loading...</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-white/10">
                      <th className="text-left py-3 px-4 text-white/80 font-medium">Company</th>
                      <th className="text-left py-3 px-4 text-white/80 font-medium">Domain</th>
                      <th className="text-left py-3 px-4 text-white/80 font-medium">Email Domain</th>
                      <th className="text-left py-3 px-4 text-white/80 font-medium">Handbook URL</th>
                      <th className="text-right py-3 px-4 text-white/80 font-medium">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {companies.map(company => (
                      <tr key={company.name} className="border-b border-white/5 hover:bg-white/5">
                        <td className="py-3 px-4 text-white">{company.name}</td>
                        <td className="py-3 px-4 text-white/70">{company.domain}</td>
                        <td className="py-3 px-4 text-white/70">{company.email_domain}</td>
                        <td className="py-3 px-4 text-white/50 text-sm">
                          <a
                            href={company.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-blue-400 hover:text-blue-300"
                          >
                            View PDF
                          </a>
                        </td>
                        <td className="py-3 px-4 text-right">
                          <button
                            onClick={() => handleBulkDeleteUsers(company.name)}
                            className="mr-3 text-red-400 hover:text-red-300 text-sm"
                            title="Delete All Users"
                          >
                            Delete All Users
                          </button>
                          <button
                            onClick={() => handleDeleteCompany(company.name)}
                            className="px-3 py-1 bg-red-500/20 hover:bg-red-500/30 border border-red-500/30 rounded text-red-300 text-sm transition-all"
                          >
                            Delete Company
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
      )}

      {/* Company Admins Tab */}
      {activeTab === 'admins' && (
        <div className="max-w-7xl mx-auto">
          <div className="glass-card bg-white/10 p-6">
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-xl font-semibold text-white">Company Administrators</h2>
              <button
                onClick={() => setShowAddAdminForm(!showAddAdminForm)}
                className="px-4 py-2 bg-white/90 hover:bg-white text-black rounded-lg transition-all"
              >
                {showAddAdminForm ? 'Cancel' : '+ Add Admin'}
              </button>
            </div>

            {/* Add Admin Form */}
            {showAddAdminForm && (
              <form onSubmit={handleAddAdmin} className="mb-6 p-4 bg-white/5 rounded-lg border border-white/10">
                <div className="grid grid-cols-2 gap-4">
                  <input
                    type="email"
                    placeholder="Email"
                    value={newAdmin.email}
                    onChange={(e) => setNewAdmin({ ...newAdmin, email: e.target.value })}
                    required
                    className="px-4 py-2 bg-white/10 border border-white/10 rounded-lg text-white placeholder-white/40"
                  />
                  <input
                    type="password"
                    placeholder="Password"
                    value={newAdmin.password}
                    onChange={(e) => setNewAdmin({ ...newAdmin, password: e.target.value })}
                    required
                    className="px-4 py-2 bg-white/10 border border-white/10 rounded-lg text-white placeholder-white/40"
                  />
                  <input
                    type="text"
                    placeholder="Name"
                    value={newAdmin.name}
                    onChange={(e) => setNewAdmin({ ...newAdmin, name: e.target.value })}
                    required
                    className="px-4 py-2 bg-white/10 border border-white/10 rounded-lg text-white placeholder-white/40"
                  />
                  <select
                    value={newAdmin.company}
                    onChange={(e) => setNewAdmin({ ...newAdmin, company: e.target.value })}
                    required
                    className="px-4 py-2 bg-white/10 border border-white/10 rounded-lg text-white"
                  >
                    <option value="">Select Company</option>
                    {companies.map(c => (
                      <option key={c.name} value={c.name}>{c.name}</option>
                    ))}
                  </select>
                </div>
                <button
                  type="submit"
                  className="mt-4 px-6 py-2 bg-white/90 hover:bg-white text-black rounded-lg transition-all"
                >
                  Add Administrator
                </button>
              </form>
            )}

            {/* Change Password Modal */}
            {passwordChangeData.email && (
              <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50">
                <div className="bg-[#1a1a24] p-6 rounded-xl border border-white/10 w-full max-w-md shadow-2xl">
                  <h3 className="text-xl font-bold text-white mb-4">Change Password</h3>
                  <p className="text-white/60 mb-4">New password for <span className="text-white">{passwordChangeData.email}</span></p>

                  <form onSubmit={handleChangePassword}>
                    <input
                      type="password"
                      placeholder="New Password"
                      value={passwordChangeData.newPassword}
                      onChange={(e) => setPasswordChangeData({ ...passwordChangeData, newPassword: e.target.value })}
                      required
                      className="w-full px-4 py-2 bg-white/5 border border-white/10 rounded-lg text-white mb-6 focus:outline-none focus:border-blue-500"
                    />
                    <div className="flex justify-end gap-3">
                      <button
                        type="button"
                        onClick={() => setPasswordChangeData({ email: null, newPassword: '' })}
                        className="px-4 py-2 hover:bg-white/5 text-white/70 rounded-lg transition-all"
                      >
                        Cancel
                      </button>
                      <button
                        type="submit"
                        className="px-4 py-2 bg-blue-500 hover:bg-blue-600 text-white rounded-lg transition-all"
                      >
                        Update Password
                      </button>
                    </div>
                  </form>
                </div>
              </div>
            )}

            {/* Table */}
            {isLoading ? (
              <p className="text-white/60 text-center py-8">Loading...</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-white/10">
                      <th className="text-left py-3 px-4 text-white/80 font-medium">Name</th>
                      <th className="text-left py-3 px-4 text-white/80 font-medium">Email</th>
                      <th className="text-left py-3 px-4 text-white/80 font-medium">Company</th>
                      <th className="text-left py-3 px-4 text-white/80 font-medium">Created</th>
                      <th className="text-right py-3 px-4 text-white/80 font-medium">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {companyAdmins.map(admin => (
                      <tr key={admin.email} className="border-b border-white/5 hover:bg-white/5">
                        <td className="py-3 px-4 text-white">{admin.name}</td>
                        <td className="py-3 px-4 text-white/70">{admin.email}</td>
                        <td className="py-3 px-4 text-white/70">{admin.company}</td>
                        <td className="py-3 px-4 text-white/50 text-sm">
                          {admin.created_at ? new Date(admin.created_at).toLocaleDateString() : 'N/A'}
                        </td>
                        <td className="py-3 px-4 text-right">
                          <button
                            onClick={() => setPasswordChangeData({ email: admin.email, newPassword: '' })}
                            className="mr-3 px-3 py-1 bg-blue-500/20 hover:bg-blue-500/30 border border-blue-500/30 rounded text-blue-300 text-sm transition-all"
                          >
                            Pass
                          </button>
                          <button
                            onClick={() => handleDeleteAdmin(admin.email)}
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
          </div>
        </div>
      )}

      {/* Monitoring Tab */}
      {activeTab === 'monitoring' && (
        <div className="max-w-7xl mx-auto">
          <div className="glass-card bg-white/10 overflow-hidden rounded-xl">
            <MonitoringDashboard userRole="super_admin" />
          </div>
        </div>
      )}
    </div>
  );
};

export default SuperAdminDashboard;