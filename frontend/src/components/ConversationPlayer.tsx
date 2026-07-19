import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useEffect, useRef, useState } from "react";

import type { RingMode } from "./VoiceRing";

type Line = {
  speaker: "worker" | "agent" | "sys";
  text: string;
  hold: number; // ms to hold after the line finishes
};

const SCRIPT: Line[] = [
  { speaker: "sys", text: "CALL OPEN · ST. MARY'S ICU · 02:14:07", hold: 900 },
  { speaker: "worker", text: "How many sick days do I have left this year?", hold: 700 },
  { speaker: "sys", text: "ROUTED → BENEFITS · 3 PASSAGES RETRIEVED · TENANT-FENCED", hold: 900 },
  { speaker: "agent", text: "You have 4 of 10 sick days remaining. They reset on January 1 — Employee Handbook, sick leave, p. 42.", hold: 1600 },
  { speaker: "worker", text: "Book me the next two days off.", hold: 700 },
  { speaker: "sys", text: "PTO AGENT · BALANCE OK · NO BLACKOUT DATES", hold: 900 },
  { speaker: "agent", text: "Done. Tuesday and Wednesday are booked — request PTO-2381, pending your manager's approval.", hold: 2400 },
  { speaker: "sys", text: "CALL CLOSED · 41 SECONDS · 0 FORMS", hold: 2600 },
];

const TYPE_MS = 26;

/**
 * A self-playing transcript of one night-shift call. Human turns are set
 * in serif italic, the machine's log lines in mono. Notifies the parent
 * who currently holds the floor so the ring can move with them.
 */
export default function ConversationPlayer({
  onMode,
}: {
  onMode: (m: RingMode) => void;
}) {
  const reduce = useReducedMotion();
  const [idx, setIdx] = useState(0);
  const [chars, setChars] = useState(0);
  const timer = useRef<number>();

  useEffect(() => {
    if (reduce) return;
    const line = SCRIPT[idx];
    onMode(line.speaker === "sys" ? "idle" : (line.speaker as RingMode));
    setChars(0);

    const isTyped = line.speaker !== "sys";
    let n = 0;
    const tick = () => {
      n += 1;
      setChars(n);
      if (isTyped && n < line.text.length) {
        timer.current = window.setTimeout(tick, TYPE_MS);
      } else {
        timer.current = window.setTimeout(() => {
          setIdx((i) => (i + 1) % SCRIPT.length);
        }, line.hold);
      }
    };
    timer.current = window.setTimeout(tick, isTyped ? 260 : 60);
    return () => window.clearTimeout(timer.current);
  }, [idx, reduce, onMode]);

  if (reduce) {
    // static full transcript for reduced motion
    return (
      <div className="flex max-w-md flex-col gap-3 text-center">
        {SCRIPT.map((l, i) => (
          <p key={i} className={lineClass(l.speaker)}>
            {l.text}
          </p>
        ))}
      </div>
    );
  }

  const line = SCRIPT[idx];
  const shown =
    line.speaker === "sys" ? line.text : line.text.slice(0, chars);

  return (
    <div className="flex h-40 max-w-md flex-col items-center justify-center gap-4 text-center sm:h-44">
      <span className="trace text-ash">
        {line.speaker === "worker"
          ? "MAYA K · REGISTERED NURSE"
          : line.speaker === "agent"
            ? "FRONTSHIFT · CONCIERGE"
            : "SYSTEM"}
      </span>
      <AnimatePresence mode="wait">
        <motion.p
          key={idx}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.18 }}
          className={lineClass(line.speaker)}
        >
          {shown}
          {line.speaker !== "sys" && chars < line.text.length && (
            <span className="ml-0.5 inline-block h-[1em] w-px translate-y-[0.15em] bg-current" />
          )}
        </motion.p>
      </AnimatePresence>
    </div>
  );
}

function lineClass(speaker: Line["speaker"]) {
  if (speaker === "worker")
    return "voice text-2xl leading-snug text-hivis sm:text-[1.7rem]";
  if (speaker === "agent")
    return "text-base leading-relaxed text-paper sm:text-lg";
  return "trace text-steel";
}
