import React, { useState, useEffect } from 'react';
import {
    LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
    PieChart, Pie, Cell, BarChart, Bar
} from 'recharts';
import { getMonitoringStats } from '../services/api';

const COLORS = ['#0088FE', '#00C49F', '#FFBB28', '#FF8042', '#8884d8'];

const MonitoringDashboard = ({ userRole }) => {
    const [stats, setStats] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [timeRange, setTimeRange] = useState('7d');

    useEffect(() => {
        fetchStats();
    }, [timeRange]);

    const fetchStats = async () => {
        setLoading(true);
        try {
            const data = await getMonitoringStats(timeRange);
            setStats(data);
            setError(null);
        } catch (err) {
            console.error("Failed to fetch stats:", err);
            setError("Failed to load monitoring data. Please try again.");
        } finally {
            setLoading(false);
        }
    };

    if (loading && !stats) {
        return (
            <div className="flex justify-center items-center h-64">
                <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500"></div>
            </div>
        );
    }

    if (error) {
        return (
            <div className="bg-red-50 border-l-4 border-red-500 p-4 m-4">
                <div className="flex">
                    <div className="flex-shrink-0">
                        <svg className="h-5 w-5 text-red-400" viewBox="0 0 20 20" fill="currentColor">
                            <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
                        </svg>
                    </div>
                    <div className="ml-3">
                        <p className="text-sm text-red-700">{error}</p>
                        <button
                            onClick={fetchStats}
                            className="mt-2 text-sm font-medium text-red-700 hover:text-red-600 underline"
                        >
                            Retry
                        </button>
                    </div>
                </div>
            </div>
        );
    }

    const isSuperAdmin = userRole === 'super_admin';

    return (
        <div className="p-6 min-h-screen text-white">
            <div className="flex justify-between items-center mb-8">
                <div>
                    <h1 className="text-2xl font-bold text-white">
                        {isSuperAdmin ? 'System Monitoring' : 'Company Monitoring'}
                    </h1>
                    <p className="text-sm text-gray-400 mt-1">Real-time system performance and usage metrics</p>
                </div>

                <div className="flex bg-white/10 rounded-lg p-1 border border-white/10">
                    {['24h', '7d', '30d'].map((range) => (
                        <button
                            key={range}
                            onClick={() => setTimeRange(range)}
                            className={`px-4 py-2 text-sm font-medium rounded-md transition-colors ${timeRange === range
                                ? 'bg-blue-600 text-white'
                                : 'text-gray-400 hover:text-white hover:bg-white/5'
                                }`}
                        >
                            {range === '24h' ? 'Last 24 Hours' : range === '7d' ? 'Last 7 Days' : 'Last 30 Days'}
                        </button>
                    ))}
                </div>
            </div>

            {/* Operational Stats (Company Admin Only) */}
            {!isSuperAdmin && stats?.operational_stats && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
                    <div className="bg-gray-900/50 p-6 rounded-xl border border-l-4 border-yellow-400 border-t-0 border-r-0 border-b-0">
                        <p className="text-sm font-medium text-gray-400 mb-1">Pending PTO Requests</p>
                        <h3 className="text-3xl font-bold text-white">{stats.operational_stats.pending_pto}</h3>
                    </div>
                    <div className="bg-gray-900/50 p-6 rounded-xl border border-l-4 border-blue-400 border-t-0 border-r-0 border-b-0">
                        <p className="text-sm font-medium text-gray-400 mb-1">Open HR Tickets</p>
                        <h3 className="text-3xl font-bold text-white">{stats.operational_stats.open_tickets}</h3>
                    </div>
                </div>
            )}

            {/* Overview Cards */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
                <StatCard
                    title="Total Requests"
                    value={stats?.overview.total_requests.toLocaleString()}
                    icon="📊"
                    trend="neutral"
                />
                <StatCard
                    title="Active Users"
                    value={stats?.overview.active_users}
                    icon="👥"
                    subtext="Last 30 days"
                />
                <StatCard
                    title="Avg Response Time"
                    value={stats?.overview.avg_response_time}
                    icon="⚡"
                    trend="good"
                />
                <StatCard
                    title="Error Rate"
                    value={`${stats?.overview.error_rate}%`}
                    icon="⚠️"
                    trend={stats?.overview.error_rate > 1 ? 'bad' : 'good'}
                    isError={true}
                />
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 mb-8">
                {/* Requests Over Time */}
                <div className="bg-gray-900/50 p-6 rounded-xl border border-white/10">
                    <h3 className="text-lg font-semibold text-white mb-6">Request Volume</h3>
                    <div className="h-80">
                        <ResponsiveContainer width="100%" height="100%">
                            <LineChart data={stats?.charts.requests_over_time}>
                                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#374151" />
                                <XAxis
                                    dataKey="date"
                                    axisLine={false}
                                    tickLine={false}
                                    tick={{ fill: '#9CA3AF', fontSize: 12 }}
                                    dy={10}
                                />
                                <YAxis
                                    axisLine={false}
                                    tickLine={false}
                                    tick={{ fill: '#9CA3AF', fontSize: 12 }}
                                />
                                <Tooltip
                                    contentStyle={{ backgroundColor: '#1F2937', borderRadius: '8px', border: 'none', color: '#fff' }}
                                    itemStyle={{ color: '#fff' }}
                                />
                                <Line
                                    type="monotone"
                                    dataKey="requests"
                                    stroke="#3B82F6"
                                    strokeWidth={3}
                                    dot={{ fill: '#3B82F6', strokeWidth: 2 }}
                                    activeDot={{ r: 6 }}
                                />
                            </LineChart>
                        </ResponsiveContainer>
                    </div>
                </div>

                {/* Agent Usage (Super Admin Only) */}
                {isSuperAdmin && (
                    <div className="bg-gray-900/50 p-6 rounded-xl border border-white/10">
                        <h3 className="text-lg font-semibold text-white mb-6">Agent Usage Distribution</h3>
                        <div className="h-80">
                            <ResponsiveContainer width="100%" height="100%">
                                <PieChart>
                                    <Pie
                                        data={stats?.charts.agent_usage}
                                        cx="50%"
                                        cy="50%"
                                        innerRadius={80}
                                        outerRadius={120}
                                        fill="#8884d8"
                                        paddingAngle={5}
                                        dataKey="value"
                                    >
                                        {stats?.charts.agent_usage.map((entry, index) => (
                                            <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                                        ))}
                                    </Pie>
                                    <Tooltip
                                        contentStyle={{ backgroundColor: '#1F2937', borderRadius: '8px', border: 'none', color: '#fff' }}
                                        itemStyle={{ color: '#fff' }}
                                    />
                                    <Legend verticalAlign="bottom" height={36} wrapperStyle={{ color: '#9CA3AF' }} />
                                </PieChart>
                            </ResponsiveContainer>
                        </div>
                    </div>
                )}

                {/* Top Users (Company Admin Only) */}
                {!isSuperAdmin && (
                    <div className="bg-gray-900/50 p-6 rounded-xl border border-white/10">
                        <h3 className="text-lg font-semibold text-white mb-6">Top Active Users</h3>
                        <div className="overflow-x-auto">
                            <table className="min-w-full divide-y divide-gray-800">
                                <thead className="bg-gray-800/50">
                                    <tr>
                                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">User</th>
                                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">Requests</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-gray-800">
                                    {stats?.top_users && stats.top_users.map((user, idx) => (
                                        <tr key={idx}>
                                            <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-white">{user.email}</td>
                                            <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-400">{user.requests}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                )}
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                {/* Response Time Distribution */}
                <div className="lg:col-span-1 bg-gray-900/50 p-6 rounded-xl border border-white/10">
                    <h3 className="text-lg font-semibold text-white mb-6">Response Time</h3>
                    <div className="h-64">
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={stats?.charts.response_time_distribution} layout="vertical">
                                <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#374151" />
                                <XAxis type="number" hide />
                                <YAxis
                                    dataKey="range"
                                    type="category"
                                    axisLine={false}
                                    tickLine={false}
                                    width={60}
                                    tick={{ fill: '#9CA3AF', fontSize: 12 }}
                                />
                                <Tooltip
                                    cursor={{ fill: '#374151' }}
                                    contentStyle={{ backgroundColor: '#1F2937', borderRadius: '8px', border: 'none', color: '#fff' }}
                                />
                                <Bar dataKey="count" fill="#10B981" radius={[0, 4, 4, 0]} barSize={20} />
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                </div>

                {/* Recent Errors (Super Admin Only) */}
                {isSuperAdmin && (
                    <div className="lg:col-span-2 bg-gray-900/50 p-6 rounded-xl border border-white/10">
                        <h3 className="text-lg font-semibold text-white mb-4">Recent System Errors</h3>
                        <div className="overflow-x-auto">
                            <table className="min-w-full divide-y divide-gray-800">
                                <thead className="bg-gray-800/50">
                                    <tr>
                                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">Time</th>
                                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">Type</th>
                                        <th className="px-6 py-3 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">Error</th>
                                    </tr>
                                </thead>
                                <tbody className="divide-y divide-gray-800">
                                    {stats?.recent_errors && stats.recent_errors.length > 0 ? (
                                        stats.recent_errors.map((err) => (
                                            <tr key={err.id}>
                                                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-400">
                                                    {new Date(err.time).toLocaleString()}
                                                </td>
                                                <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-white">
                                                    {err.type || 'System'}
                                                </td>
                                                <td className="px-6 py-4 text-sm text-red-400 max-w-xs truncate">
                                                    {err.error}
                                                </td>
                                            </tr>
                                        ))
                                    ) : (
                                        <tr>
                                            <td colSpan="3" className="px-6 py-8 text-center text-sm text-gray-500">
                                                No recent errors found 🎉
                                            </td>
                                        </tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    </div>
                )}

                {/* Company Activity (Super Admin Only) */}
                {isSuperAdmin && (
                    <div className="lg:col-span-2 bg-gray-900/50 p-6 rounded-xl border border-white/10">
                        <h3 className="text-lg font-semibold text-white mb-6">Top Companies by Activity</h3>
                        <div className="h-64">
                            <ResponsiveContainer width="100%" height="100%">
                                <BarChart data={stats?.charts.company_activity} layout="vertical">
                                    <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#374151" />
                                    <XAxis type="number" hide />
                                    <YAxis
                                        dataKey="name"
                                        type="category"
                                        axisLine={false}
                                        tickLine={false}
                                        width={100}
                                        tick={{ fill: '#9CA3AF', fontSize: 12 }}
                                    />
                                    <Tooltip
                                        cursor={{ fill: '#374151' }}
                                        contentStyle={{ backgroundColor: '#1F2937', borderRadius: '8px', border: 'none', color: '#fff' }}
                                    />
                                    <Bar dataKey="requests" fill="#8884d8" radius={[0, 4, 4, 0]} barSize={20} />
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};

const StatCard = ({ title, value, icon, subtext, trend, isError }) => (
    <div className="bg-gray-900/50 p-6 rounded-xl border border-white/10 flex items-start justify-between">
        <div>
            <p className="text-sm font-medium text-gray-400 mb-1">{title}</p>
            <h3 className={`text-2xl font-bold ${isError && parseFloat(value) > 0 ? 'text-red-400' : 'text-white'}`}>
                {value}
            </h3>
            {subtext && <p className="text-xs text-gray-500 mt-1">{subtext}</p>}
        </div>
        <div className={`p-3 rounded-lg ${isError ? 'bg-red-900/20 text-red-400' : 'bg-blue-900/20 text-blue-400'}`}>
            <span className="text-xl">{icon}</span>
        </div>
    </div>
);

export default MonitoringDashboard;
