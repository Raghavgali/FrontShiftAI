import React, { useState } from 'react';
import { login } from '../services/api';
import { ArrowLeft } from 'lucide-react';

import FrontShiftLogo from './FrontShiftLogo';

const Login = ({ onLoginSuccess, onBack }) => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setIsLoading(true);

    try {
      const response = await login(email, password);

      console.log('🔐 Login response:', response);  // DEBUG

      // Store auth data in localStorage
      localStorage.setItem('access_token', response.access_token);
      localStorage.setItem('user_email', response.email);
      localStorage.setItem('user_company', response.company);
      localStorage.setItem('user_name', response.name || 'User');
      localStorage.setItem('user_role', response.role);  // ← ADDED THIS LINE

      // Notify parent component
      onLoginSuccess(response);
    } catch (err) {
      console.error('Login error:', err);
      if (err.response?.status === 401) {
        setError('Invalid email or password');
      } else {
        setError('Login failed. Please try again.');
      }
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-[#0a0a0f] via-[#1a1a24] to-[#0a0a0f] flex items-center justify-center p-4">
      {/* Floating Orb Background */}
      <div className="fixed top-1/4 right-1/4 w-96 h-96 bg-gradient-to-r from-white/10 to-gray-500/10 rounded-full blur-3xl opacity-20 animate-float-orb pointer-events-none"></div>

      <div className="w-full max-w-md relative z-10">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center mb-4">
            <FrontShiftLogo size={48} showText={true} />
          </div>
          <p className="text-white/60 text-sm">Sign in to access your company's handbook</p>
        </div>

        {/* Login Card */}
        <div className="glass-card bg-white/10 backdrop-blur-xl p-8">
          <h2 className="text-xl font-semibold text-white mb-6">Welcome Back</h2>

          <form onSubmit={handleSubmit} className="space-y-5">
            {/* Email Input */}
            <div>
              <label className="block text-sm font-medium text-white/80 mb-2">
                Email Address
              </label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="user@company.com"
                required
                className="w-full px-4 py-3 bg-white/5 border border-white/10 rounded-lg text-white placeholder-white/40 focus:outline-none focus:border-white/30 focus:bg-white/8 transition-all"
              />
            </div>

            {/* Password Input */}
            <div>
              <label className="block text-sm font-medium text-white/80 mb-2">
                Password
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter your password"
                required
                className="w-full px-4 py-3 bg-white/5 border border-white/10 rounded-lg text-white placeholder-white/40 focus:outline-none focus:border-white/30 focus:bg-white/8 transition-all"
              />
            </div>

            {/* Error Message */}
            {error && (
              <div className="p-3 bg-red-500/10 border border-red-500/30 rounded-lg">
                <p className="text-sm text-red-300">{error}</p>
              </div>
            )}

            {/* Submit Button */}
            <button
              type="submit"
              disabled={isLoading}
              className="w-full py-3 bg-gradient-to-br from-white/90 to-white/60 text-black font-medium rounded-lg shadow-[0_12px_25px_rgba(15,15,20,0.55)] hover:shadow-[0_15px_30px_rgba(15,15,20,0.65)] disabled:opacity-50 disabled:cursor-not-allowed transition-all active:scale-[0.98]"
            >
              {isLoading ? 'Signing in...' : 'Sign In'}
            </button>
          </form>

          {/* Demo Credentials */}
          <div className="mt-6 pt-6 border-t border-white/10">
            <p className="text-xs text-white/40 mb-3">Demo Credentials:</p>
            <div className="space-y-3">
              <div className="bg-white/5 p-3 rounded-lg">
                <p className="text-xs text-white/50 mb-1">Regular User:</p>
                <p className="text-xs text-white/70">user@crousemedical.com / password123</p>
              </div>
              <div className="bg-white/5 p-3 rounded-lg">
                <p className="text-xs text-white/50 mb-1">Company Admin:</p>
                <p className="text-xs text-white/70">admin@crousemedical.com / admin123</p>
              </div>
              <div className="bg-white/5 p-3 rounded-lg">
                <p className="text-xs text-white/50 mb-1">Super Admin:</p>
                <p className="text-xs text-white/70">admin@group9.com / admin123</p>
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <p className="text-center text-xs text-white/30 mt-6">
          Secure access to company handbooks and policies
        </p>
      </div>

      {/* Back Button */}
      <button
        onClick={onBack}
        className="absolute top-6 left-6 p-2 rounded-full bg-white/5 border border-white/10 text-white/60 hover:text-white hover:bg-white/10 transition-all z-50 group"
      >
        <ArrowLeft size={20} className="group-hover:-translate-x-0.5 transition-transform" />
      </button>
    </div>
  );
};

export default Login;