import { motion, useScroll, useTransform, type MotionValue } from "framer-motion";
import { useRef } from "react";

const STAGES = [
  {
    name: "LISTEN",
    budget: "0.3S END-OF-SPEECH",
    body: "The mic decides when you are done talking. Voice activity detection closes the gap after 0.3 seconds of silence, so the pipeline starts before you wonder if it heard you.",
    tech: "SILERO VAD · LIVEKIT · MODAL",
  },
  {
    name: "ROUTE",
    budget: "ONE HOP",
    body: "One classifier reads the request and hands it to the right specialist: a handbook question, a PTO action, an HR ticket, or a web search. No menus, no 'press 2 for benefits'.",
    tech: "GROQ · LLAMA 3.1 8B INSTANT",
  },
  {
    name: "RETRIEVE",
    budget: "YOUR SHELF ONLY",
    body: "Search runs against your company's documents and nobody else's — a vector collection per tenant, reranked, with the source page kept for the citation.",
    tech: "CHROMADB · PER-TENANT COLLECTIONS · RERANKER",
  },
  {
    name: "GENERATE",
    budget: "STREAMED, NEVER STRANDED",
    body: "The answer streams token by token over SSE. If a model provider goes down mid-sentence, the request falls through a chain of backups instead of failing.",
    tech: "MERCURY → GROQ → OPENAI · SSE",
  },
  {
    name: "SPEAK",
    budget: "BACK ON THE CALL",
    body: "Text-to-speech reads the answer onto the line, citation and all. On chat, the same stream renders as it is generated. Either way, the worker never saw a portal.",
    tech: "TTS · LIVEKIT AGENTS · CITED SOURCES",
  },
];

/**
 * Chapter 02 — the request slowed down to five stages, told as a pinned
 * card stack (Skiper 16 pattern).
 */
const StageCard = ({
  i,
  stage,
  progress,
  targetScale,
}: {
  i: number;
  stage: (typeof STAGES)[number];
  progress: MotionValue<number>;
  targetScale: number;
}) => {
  const scale = useTransform(progress, [i * 0.2, 1], [1, targetScale]);

  return (
    <div className="sticky top-0 flex h-svh items-center justify-center">
      <motion.div
        style={{ scale, top: `calc(-3vh + ${i * 26}px)` }}
        className="relative flex min-h-[420px] w-[min(92vw,860px)] origin-top flex-col justify-between border border-white/15 bg-ink p-8 text-paper sm:p-12"
      >
        <div className="trace flex items-center justify-between text-ash">
          <span>
            STAGE {String(i + 1).padStart(2, "0")}/{String(STAGES.length).padStart(2, "0")}
          </span>
          <span className="text-right">{stage.budget}</span>
        </div>
        <div className="py-10">
          <h3 className="display text-[clamp(3rem,9vw,6.5rem)]">{stage.name}</h3>
          <p className="mt-6 max-w-xl text-base leading-relaxed text-ash sm:text-lg">
            {stage.body}
          </p>
        </div>
        <div className="trace border-t border-white/10 pt-5 text-steel">
          {stage.tech}
        </div>
      </motion.div>
    </div>
  );
};

export default function Pipeline() {
  const container = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({
    target: container,
    offset: ["start start", "end end"],
  });

  return (
    <section id="pipeline" className="scroll-mt-10 bg-silver text-ink">
      <div className="mx-auto max-w-5xl px-6 pt-28 text-center">
        <p className="trace text-steel">02 — THE PIPELINE</p>
        <h2 className="display mt-6 text-[clamp(2.2rem,6vw,4.8rem)]">
          The same minute,
          <br />
          slowed down
        </h2>
        <p className="mx-auto mt-6 max-w-xl text-base leading-relaxed text-steel">
          Five stages between a spoken question and a spoken answer. Every
          number below is the real budget the stage is engineered against.
        </p>
      </div>

      <div ref={container} className="relative pb-[24svh]">
        {STAGES.map((stage, i) => {
          const targetScale = Math.max(0.72, 1 - (STAGES.length - i - 1) * 0.06);
          return (
            <StageCard
              key={stage.name}
              i={i}
              stage={stage}
              progress={scrollYProgress}
              targetScale={targetScale}
            />
          );
        })}
      </div>
    </section>
  );
}
