import { motion } from "framer-motion";
import { useState } from "react";

import { cn } from "@/lib/utils";

const AGENTS = [
  {
    name: "ROUTER",
    ask: "…any question at all.",
    body: "Reads every request first and dispatches it to the right specialist in a single model call.",
    tech: "GROQ · ONE-HOP CLASSIFIER",
  },
  {
    name: "LIBRARIAN",
    ask: "What's the overtime policy on holidays?",
    body: "Searches your company's handbook shelf, reranks what it finds, and answers with the page number attached.",
    tech: "RAG · CITED PASSAGES",
  },
  {
    name: "PTO DESK",
    ask: "Can I take Friday and Monday off?",
    body: "Checks your live balance, validates dates against holidays and blackout windows, then books it — with a request ID.",
    tech: "LANGGRAPH · LIVE BALANCES",
  },
  {
    name: "HR DESK",
    ask: "I didn't get my shift differential this week.",
    body: "Files a categorized, prioritized HR ticket and tells you where you are in the queue.",
    tech: "TICKETS · QUEUE POSITION",
  },
  {
    name: "RESEARCHER",
    ask: "Is the downtown clinic open Saturdays?",
    body: "When the handbook has no answer, it searches your company's public website instead of guessing.",
    tech: "WEB SEARCH · TENANT SITES",
  },
];

/**
 * Chapter 03 — five specialists as an expanding strip (Skiper 52 pattern,
 * rebuilt with text panels instead of images).
 */
export default function Crew() {
  const [active, setActive] = useState(2);

  return (
    <section id="crew" className="scroll-mt-10 bg-silver px-6 py-28 text-ink">
      <div className="mx-auto max-w-6xl">
        <p className="trace text-steel">03 — THE CREW</p>
        <h2 className="display mt-6 max-w-3xl text-[clamp(2.2rem,6vw,4.8rem)]">
          One number to call, five specialists behind it
        </h2>
        <p className="mt-6 max-w-xl text-base leading-relaxed text-steel">
          Each panel is a real agent in the codebase, introduced by the kind
          of sentence that wakes it up.
        </p>

        {/* Desktop: expanding strip */}
        <div className="mt-14 hidden gap-1.5 lg:flex">
          {AGENTS.map((agent, index) => (
            <motion.button
              key={agent.name}
              type="button"
              className={cn(
                "relative h-[27rem] cursor-pointer overflow-hidden border text-left",
                active === index
                  ? "border-ink bg-ink text-paper"
                  : "hairline-dark bg-transparent text-ink"
              )}
              initial={false}
              animate={{ width: active === index ? "34rem" : "5.5rem" }}
              transition={{ duration: 0.4, ease: [0.32, 0.72, 0, 1] }}
              onClick={() => setActive(index)}
              onHoverStart={() => setActive(index)}
              onFocus={() => setActive(index)}
            >
              {active === index ? (
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ duration: 0.3, delay: 0.15 }}
                  className="flex h-full w-[34rem] flex-col justify-between p-8"
                >
                  <span className="trace text-ash">
                    AGENT {String(index + 1).padStart(2, "0")}/05
                  </span>
                  <div>
                    <p className="voice text-2xl text-silver">“{agent.ask}”</p>
                    <h3 className="display mt-6 text-5xl">{agent.name}</h3>
                    <p className="mt-5 max-w-sm text-sm leading-relaxed text-ash">
                      {agent.body}
                    </p>
                  </div>
                  <span className="trace border-t border-white/10 pt-4 text-steel">
                    {agent.tech}
                  </span>
                </motion.div>
              ) : (
                <div className="flex h-full flex-col items-center justify-between py-6">
                  <span className="trace">{String(index + 1).padStart(2, "0")}</span>
                  <span
                    className="trace tracking-[0.3em]"
                    style={{ writingMode: "vertical-rl" }}
                  >
                    {agent.name}
                  </span>
                  <span className="h-6 w-px bg-current opacity-30" />
                </div>
              )}
            </motion.button>
          ))}
        </div>

        {/* Mobile: stacked list */}
        <div className="mt-12 flex flex-col gap-3 lg:hidden">
          {AGENTS.map((agent, index) => (
            <div key={agent.name} className="border hairline-dark bg-ink p-6 text-paper">
              <span className="trace text-ash">
                AGENT {String(index + 1).padStart(2, "0")}/05 — {agent.name}
              </span>
              <p className="voice mt-4 text-xl text-silver">“{agent.ask}”</p>
              <p className="mt-3 text-sm leading-relaxed text-ash">{agent.body}</p>
              <p className="trace mt-4 text-steel">{agent.tech}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
