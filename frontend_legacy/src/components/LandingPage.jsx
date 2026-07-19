import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { MessageSquare, Calendar, Ticket, Search, ArrowRight, Menu, X, Brain, Shield, Zap } from 'lucide-react';

import FrontShiftLogo from './FrontShiftLogo';

const LandingPage = ({ onGetStarted }) => {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [videoKey, setVideoKey] = useState(Date.now());

  const scrollToSection = (id) => {
    const element = document.getElementById(id);
    if (element) {
      element.scrollIntoView({ behavior: 'smooth' });
      setMobileMenuOpen(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#0A0E1A] relative overflow-hidden">
      {/* Video Background */}
      <div className="video-container">
        <video
          key={videoKey}
          autoPlay
          muted
          loop
          playsInline
          preload="auto"
          onLoadedData={(e) => {
            e.target.play().catch(err => console.log('Video play error:', err));
          }}
          onError={(e) => {
            console.error('Video load error:', e);
          }}
        >
          <source src={`/metallic_gradient_output.mp4?v=${videoKey}`} type="video/mp4" />
        </video>
        <div className="video-overlay"></div>
      </div>

      {/* Navigation */}
      <nav className="relative z-50">
        <div className="max-w-7xl mx-auto px-6 py-6">
          <motion.div
            initial={{ y: -20, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            transition={{ duration: 0.5 }}
            className="glass-card rounded-2xl px-6 py-4 flex justify-between items-center"
          >
            <FrontShiftLogo size={40} showText={true} />

            {/* Desktop Menu */}
            <div className="hidden md:flex gap-8 items-center">
              <button
                onClick={() => scrollToSection('features')}
                className="text-white/90 hover:text-white transition text-lg font-medium"
              >
                Features
              </button>
              <button
                onClick={() => scrollToSection('how-it-works')}
                className="text-white/90 hover:text-white transition text-lg font-medium"
              >
                How It Works
              </button>
              <button
                onClick={() => scrollToSection('about')}
                className="text-white/90 hover:text-white transition text-lg font-medium"
              >
                About
              </button>
              <button
                onClick={onGetStarted}
                className="bg-[#E0E0E0] text-black px-6 py-2 rounded-xl hover:bg-white transition font-semibold"
              >
                Get Started
              </button>
            </div>

            {/* Mobile Menu Button */}
            <button
              className="md:hidden text-white"
              onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            >
              {mobileMenuOpen ? <X size={24} /> : <Menu size={24} />}
            </button>
          </motion.div>

          {/* Mobile Menu */}
          {mobileMenuOpen && (
            <motion.div
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              className="md:hidden mt-4 glass-card rounded-2xl px-6 py-4"
            >
              <div className="flex flex-col gap-4">
                <button
                  onClick={() => scrollToSection('features')}
                  className="text-white/90 hover:text-white text-left"
                >
                  Features
                </button>
                <button
                  onClick={() => scrollToSection('how-it-works')}
                  className="text-white/90 hover:text-white text-left"
                >
                  How It Works
                </button>
                <button
                  onClick={() => scrollToSection('about')}
                  className="text-white/90 hover:text-white text-left"
                >
                  About
                </button>
                <button
                  onClick={onGetStarted}
                  className="bg-[#E0E0E0] text-black px-6 py-2 rounded-xl border border-white/20 font-semibold"
                >
                  Get Started
                </button>
              </div>
            </motion.div>
          )}
        </div>
      </nav>

      {/* Hero Section */}
      <section className="relative z-10 pt-20 pb-32 px-6 min-h-screen flex items-center">
        <div className="max-w-7xl mx-auto">
          <div className="text-center max-w-4xl mx-auto">
            {/* Tag Pill */}
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6 }}
              className="inline-flex items-center gap-2 glass-card px-4 py-2 rounded-full mb-8"
            >
              <span className="text-sm text-[#9CA3AF] font-semibold">2025</span>
              <span className="text-sm text-white/90">Context-Aware Intelligence</span>
            </motion.div>

            {/* Main Headline */}
            <motion.h1
              initial={{ opacity: 0, y: 30 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.8, delay: 0.2 }}
              className="text-6xl md:text-7xl lg:text-8xl font-bold mb-6 leading-tight"
              style={{
                background: 'linear-gradient(180deg, #E8E8E8 0%, #C0C0C0 25%, #A8A8A8 50%, #C0C0C0 75%, #E8E8E8 100%)',
                WebkitBackgroundClip: 'text',
                backgroundClip: 'text',
                WebkitTextFillColor: 'transparent',
                color: 'transparent'
              }}
            >
              Your AI Copilot
              <br />
              For Deskless Workers
            </motion.h1>

            {/* Subtitle */}
            <motion.p
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.8, delay: 0.4 }}
              className="text-xl text-white/80 mb-12 max-w-2xl mx-auto"
            >
              Creating latest solutions that redefine innovation. Stay ahead with AI-powered technology for the future.
            </motion.p>

            {/* CTA Buttons */}
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.8, delay: 0.6 }}
              className="flex flex-col sm:flex-row gap-4 justify-center"
            >
              <motion.button
                whileHover={{ scale: 1.05, y: -2 }}
                whileTap={{ scale: 0.95 }}
                onClick={onGetStarted}
                className="group bg-gradient-to-r from-[#9CA3AF] to-[#6B7280] text-white px-8 py-4 rounded-xl font-semibold hover:shadow-[0_8px_30px_rgba(156,163,175,0.6)] transition-all shadow-[0_4px_20px_rgba(156,163,175,0.4)] flex items-center justify-center gap-2"
              >
                Get Started
                <ArrowRight className="group-hover:translate-x-1 transition-transform" size={20} />
              </motion.button>
            </motion.div>

          </div>
        </div>
      </section>

      {/* Features Section */}
      <section id="features" className="relative z-10 py-20 px-6">
        <div className="max-w-7xl mx-auto">
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.8 }}
            className="text-center mb-16"
          >
            <h2 className="text-5xl font-bold text-white mb-4">Powerful Features</h2>
            <p className="text-xl text-white/70">Everything you need to transform your workforce</p>
          </motion.div>

          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-8">
            {[
              {
                icon: <Brain size={32} />,
                title: "RAG-Powered Intelligence",
                desc: "Retrieval-Augmented Generation for accurate, document-grounded answers from company handbooks"
              },
              {
                icon: <Zap size={32} />, // Using Zap icon for Voice/Speech as requested/implied by "similar fashion" context or I can use Mic if available. The user prompt mentioned "voice feature". Let's check imports. Zap is imported.  Wait, Mic isn't imported. I should check imports or simple use Zap or maybe MessageSquare is better?  Actually, let's use Zap for 'fast/action' or maybe Brain again? No.
                // The user prompt specifically asked for voice feature. I'll use MessageSquare if Mic isn't available or maybe I can import Mic.
                // Looking at imports: MessageSquare, Calendar, Ticket, Search, ArrowRight, Menu, X, Brain, Shield, Zap.
                // Mic is NOT imported. I will use Zap for now as a placeholder for "active/voice" or simply reuse MessageSquare or Brain if suitable.
                // Actually, let's add Mic to imports first or just use Zap which is available.
                // User said "similar fashion".
                // I will add "Mic" to imports in a separate step or just use one of the existing ones.
                // Let's use Zap for now as it represents "Live/Action" often.
                title: "Voice-First Interaction",
                desc: "Hands-free accessibility with real-time speech-to-text for on-the-go deskless workers"
              },
              {
                icon: <Calendar size={32} />,
                title: "PTO Management Agent",
                desc: "Intelligent time-off request handling with automatic balance tracking and approval workflows"
              },
              {
                icon: <Ticket size={32} />,
                title: "HR Ticket System",
                desc: "Automated support ticket creation and queue management for seamless HR interactions"
              },
              {
                icon: <Search size={32} />,
                title: "Website Extraction",
                desc: "Automatic fallback to company websites when handbook information is unavailable"
              },
              {
                icon: <MessageSquare size={32} />,
                title: "Unified Chat Interface",
                desc: "Single conversation flow that intelligently routes to the right agent for any query"
              }
            ].map((feature, i) => (
              <motion.div
                key={i}
                initial={{ opacity: 0, y: 30 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ duration: 0.6, delay: i * 0.1 }}
                whileHover={{ scale: 1.05, y: -5 }}
                className="glass-card rounded-3xl p-8 hover:border-white/20 transition-all duration-300 group"
              >
                <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-[#9CA3AF]/20 to-[#6B7280]/20 flex items-center justify-center mb-6 group-hover:scale-110 transition-transform">
                  <div className="text-[#9CA3AF]">
                    {feature.icon}
                  </div>
                </div>
                <h3 className="text-2xl font-bold text-white mb-4">{feature.title}</h3>
                <p className="text-white/70">{feature.desc}</p>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* Stats Section */}
      <section className="relative z-10 py-20 px-6">
        <div className="max-w-7xl mx-auto">
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            whileInView={{ opacity: 1, scale: 1 }}
            viewport={{ once: true }}
            transition={{ duration: 0.8 }}
            className="glass-card rounded-3xl p-12"
          >
            <div className="grid md:grid-cols-3 gap-12 text-center">
              {[
                { num: "19+", label: "Companies Served" },
                { num: "3", label: "AI Agents" },
                { num: "24/7", label: "Support" }
              ].map((stat, i) => (
                <motion.div
                  key={i}
                  initial={{ opacity: 0, y: 20 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true }}
                  transition={{ duration: 0.6, delay: i * 0.1 }}
                >
                  <div className="text-5xl font-bold gradient-text-purple-blue mb-2">
                    {stat.num}
                  </div>
                  <p className="text-white/70">{stat.label}</p>
                </motion.div>
              ))}
            </div>
          </motion.div>
        </div>
      </section>

      {/* How It Works Section */}
      <section id="how-it-works" className="relative z-10 py-20 px-6">
        <div className="max-w-7xl mx-auto">
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.8 }}
            className="text-center mb-16"
          >
            <h2 className="text-5xl font-bold text-white mb-4">How It Works</h2>
            <p className="text-xl text-white/70">Simple, intelligent, and seamless</p>
          </motion.div>

          <div className="grid md:grid-cols-3 gap-8">
            {[
              {
                step: "01",
                title: "Ask Your Question",
                desc: "Simply type your question in natural language - whether it's about policies, PTO, or HR support"
              },
              {
                step: "02",
                title: "Intelligent Routing",
                desc: "Our unified agent router analyzes your intent and routes to the right agent (RAG, PTO, or HR Ticket)"
              },
              {
                step: "03",
                title: "Get Instant Answers",
                desc: "Receive accurate, context-aware responses with automatic fallback to ensure you always get an answer"
              }
            ].map((step, i) => (
              <motion.div
                key={i}
                initial={{ opacity: 0, x: i % 2 === 0 ? -30 : 30 }}
                whileInView={{ opacity: 1, x: 0 }}
                viewport={{ once: true }}
                transition={{ duration: 0.6, delay: i * 0.2 }}
                className="glass-card rounded-3xl p-8 text-center"
              >
                <div className="text-6xl font-bold gradient-text-purple-blue mb-4">
                  {step.step}
                </div>
                <h3 className="text-2xl font-bold text-white mb-4">{step.title}</h3>
                <p className="text-white/70">{step.desc}</p>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* About Section */}
      <section id="about" className="relative z-10 py-20 px-6">
        <div className="max-w-7xl mx-auto">
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.8 }}
            className="glass-card rounded-3xl p-12 text-center max-w-4xl mx-auto"
          >
            <h2 className="text-4xl font-bold text-white mb-6">About FrontShiftAI</h2>
            <p className="text-lg text-white/80 mb-6">
              FrontShiftAI is an AI copilot designed specifically for deskless workers, addressing the challenges
              of limited HR system access, irregular schedules, and fragmented communication channels.
            </p>
            <p className="text-lg text-white/80">
              Our platform combines Retrieval-Augmented Generation (RAG) with intelligent agent orchestration
              to provide context-aware responses, automate HR workflows, and ensure every employee has access
              to the information they need, when they need it.
            </p>
          </motion.div>
        </div>
      </section>

      {/* CTA Section */}
      <section className="relative z-10 py-32 px-6">
        <div className="max-w-4xl mx-auto text-center">
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            whileInView={{ opacity: 1, scale: 1 }}
            viewport={{ once: true }}
            transition={{ duration: 0.8 }}
            className="glass-card rounded-3xl p-12"
          >
            <h2 className="text-5xl font-bold text-white mb-6">
              Ready to Transform Your Workforce?
            </h2>
            <p className="text-xl text-white/70 mb-8">
              Join companies using AI to automate HR workflows and empower deskless workers
            </p>
            <motion.button
              whileHover={{ scale: 1.05, y: -2 }}
              whileTap={{ scale: 0.95 }}
              onClick={onGetStarted}
              className="bg-gradient-to-r from-[#9CA3AF] to-[#6B7280] text-white px-10 py-5 rounded-xl font-bold text-lg hover:shadow-[0_8px_30px_rgba(156,163,175,0.6)] transition-all shadow-[0_4px_20px_rgba(156,163,175,0.4)]"
            >
              Get Started
            </motion.button>
            <p className="text-white/50 text-sm mt-4">No credit card required • Start your free trial today</p>
          </motion.div>
        </div>
      </section>

      {/* Footer */}
      <footer className="relative z-10 py-12 px-6 border-t border-white/10">
        <div className="max-w-7xl mx-auto">
          <div className="flex flex-col md:flex-row justify-between items-center">
            <div className="mb-4 md:mb-0">
              <FrontShiftLogo size={32} showText={true} />
            </div>
            <p className="text-white/50 text-sm">
              © 2025 FrontShiftAI. All rights reserved.
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
};

export default LandingPage;

