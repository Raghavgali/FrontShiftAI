import { useState, useEffect, useRef, useCallback } from 'react';
import { Room, RoomEvent, Track } from 'livekit-client';
import { createVoiceSession } from '../services/api';

/**
 * Custom hook for LiveKit voice agent integration
 *
 * @param {Object} options - Configuration options
 * @param {string} options.userEmail - User email for session
 * @param {string} options.company - Company name for session
 *
 * @returns {Object} Hook interface
 */
export const useLiveKitVoice = (options = {}) => {
  const { userEmail, company } = options;

  // State
  const [status, setStatus] = useState('idle');
  const [isConnected, setIsConnected] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);
  const [error, setError] = useState(null);
  const [transcript, setTranscript] = useState('');
  const [partialText, setPartialText] = useState('');
  const [sessionId, setSessionId] = useState(null);

  // Refs for LiveKit objects
  const roomRef = useRef(null);
  const audioElementsRef = useRef([]);
  const isConnectingRef = useRef(false);
  const hasConnectedRef = useRef(false);

  /**
   * Connect to LiveKit voice agent
   */
  const connect = useCallback(async () => {
    // Prevent duplicate connections (React StrictMode fires useEffect twice)
    if (isConnectingRef.current || hasConnectedRef.current || roomRef.current) {
      console.log('⚠️ Connection already in progress or established, skipping...');
      return;
    }

    try {
      isConnectingRef.current = true;
      setIsConnecting(true);
      setError(null);

      console.log('🎙️ Creating voice session...');

      // Create session via backend API
      const session = await createVoiceSession(userEmail, company);

      console.log('✅ Voice session created:', session.session_id);
      console.log('   Room:', session.room_name);
      console.log('   LiveKit URL:', session.livekit_url);
      setSessionId(session.session_id);

      // Create LiveKit room
      const room = new Room({
        adaptiveStream: true,
        dynacast: true,
        audioCaptureDefaults: {
          autoGainControl: true,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
      roomRef.current = room;

      // === Event Listeners ===

      // Connected to room
      room.on(RoomEvent.Connected, () => {
        console.log('✅ Connected to LiveKit room');
        setIsConnected(true);
        setIsConnecting(false);
        hasConnectedRef.current = true;
        isConnectingRef.current = false;
        setStatus('idle');
      });

      // Disconnected from room
      room.on(RoomEvent.Disconnected, (reason) => {
        console.log('👋 Disconnected from voice agent, reason:', reason);
        setIsConnected(false);
        setStatus('idle');
        hasConnectedRef.current = false;
      });

      // Track subscribed (agent's audio)
      room.on(RoomEvent.TrackSubscribed, (track, publication, participant) => {
        console.log('🎵 Track subscribed:', track.kind, 'from', participant.identity);

        if (track.kind === Track.Kind.Audio) {
          // Agent is speaking - update status
          setStatus('speaking');

          // Attach agent's audio track
          const element = track.attach();
          element.autoplay = true;
          element.volume = 1.0;
          audioElementsRef.current.push(element);

          // Add to DOM (hidden)
          element.style.display = 'none';
          document.body.appendChild(element);

          console.log('🔊 Agent audio track attached');
        }
      });

      // Track unsubscribed
      room.on(RoomEvent.TrackUnsubscribed, (track, publication, participant) => {
        console.log('🔇 Track unsubscribed:', track.kind, 'from', participant.identity);
        track.detach().forEach((el) => el.remove());
        
        if (track.kind === Track.Kind.Audio) {
          // Check if agent stopped speaking
          const agentAudioTracks = room.remoteParticipants.size > 0;
          if (!agentAudioTracks) {
            setStatus('idle');
          }
        }
      });

      // Active speakers changed - detect when user/agent is speaking
      room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
        const localParticipant = room.localParticipant;
        
        // Check if local user is speaking
        const isUserSpeaking = speakers.some(
          (s) => s.identity === localParticipant?.identity
        );
        
        // Check if agent is speaking
        const isAgentSpeaking = speakers.some(
          (s) => s.identity !== localParticipant?.identity
        );

        if (isAgentSpeaking) {
          setStatus('speaking');
        } else if (isUserSpeaking) {
          setStatus('listening');
        } else if (status === 'listening' || status === 'speaking') {
          // Brief pause - might be processing
          setStatus('processing');
          // Return to idle after a short delay if nothing happens
          setTimeout(() => {
            setStatus((current) => current === 'processing' ? 'idle' : current);
          }, 2000);
        }
      });

      // Data received (transcripts, metadata from agent)
      room.on(RoomEvent.DataReceived, (payload, participant) => {
        const message = new TextDecoder().decode(payload);
        console.log('📩 Data received from', participant?.identity, ':', message);

        try {
          const data = JSON.parse(message);

          // Handle different data types
          if (data.type === 'transcript') {
            if (data.is_final) {
              setTranscript((prev) => (prev + ' ' + data.text).trim());
              setPartialText('');
            } else {
              setPartialText(data.text);
            }
          } else if (data.type === 'agent_state') {
            // Agent state updates
            if (data.state === 'thinking') {
              setStatus('processing');
            } else if (data.state === 'speaking') {
              setStatus('speaking');
            }
          }
        } catch (e) {
          // Non-JSON data
          console.log('Non-JSON data received:', message);
        }
      });

      // Local track published (confirms mic is working)
      room.on(RoomEvent.LocalTrackPublished, (publication) => {
        console.log('🎤 Local track published:', publication.kind);
        if (publication.kind === Track.Kind.Audio) {
          console.log('✅ Microphone is now active');
        }
      });

      // Participant connected (agent joined)
      room.on(RoomEvent.ParticipantConnected, (participant) => {
        console.log('👤 Participant connected:', participant.identity);
      });

      // Connection quality
      room.on(RoomEvent.ConnectionQualityChanged, (quality, participant) => {
        console.log('📶 Connection quality:', quality, 'for', participant.identity);
      });

      // Connect to LiveKit room with microphone enabled
      console.log('🔌 Connecting to LiveKit room:', session.room_name);
      
      await room.connect(session.livekit_url, session.token);
      
      console.log('✅ Room connected, enabling microphone...');
      
      // Enable microphone after connection
      await room.localParticipant.setMicrophoneEnabled(true);
      
      console.log('✅ Microphone enabled');
      console.log('🎙️ Ready to talk! Speak naturally...');

    } catch (err) {
      console.error('❌ Voice connection error:', err);
      setError(err.message || 'Failed to connect to voice agent');
      setIsConnecting(false);
      setStatus('idle');
      isConnectingRef.current = false;
      hasConnectedRef.current = false;
      roomRef.current = null;
    }
  }, [userEmail, company]);

  /**
   * Disconnect from LiveKit
   */
  const disconnect = useCallback(async () => {
    try {
      console.log('🔌 Disconnecting from voice agent...');

      // Disconnect room
      if (roomRef.current) {
        await roomRef.current.disconnect();
        roomRef.current = null;
      }

      // Clean up audio elements
      audioElementsRef.current.forEach((el) => {
        if (el.parentNode) {
          el.parentNode.removeChild(el);
        }
      });
      audioElementsRef.current = [];

      // Reset state
      setIsConnected(false);
      setIsConnecting(false);
      setStatus('idle');
      setTranscript('');
      setPartialText('');
      setSessionId(null);
      setError(null);
      hasConnectedRef.current = false;
      isConnectingRef.current = false;

      console.log('✅ Disconnected successfully');
    } catch (err) {
      console.error('❌ Disconnect error:', err);
    }
  }, []);

  /**
   * Cleanup on unmount
   */
  useEffect(() => {
    return () => {
      if (roomRef.current) {
        roomRef.current.disconnect();
        roomRef.current = null;
      }
      audioElementsRef.current.forEach((el) => el.remove());
      audioElementsRef.current = [];
      hasConnectedRef.current = false;
      isConnectingRef.current = false;
    };
  }, []);

  return {
    // State
    status,
    isConnected,
    isConnecting,
    error,
    transcript,
    partialText,
    sessionId,

    // Methods
    connect,
    disconnect,
  };
};

export default useLiveKitVoice;
