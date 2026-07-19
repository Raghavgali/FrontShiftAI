import { useState } from "react";

import ConversationPlayer from "@/components/ConversationPlayer";
import VoiceRing, { type RingMode } from "@/components/VoiceRing";
import { Cross } from "@/components/ui/cross";

/**
 * Chapter 01 — the product beauty shot. One live call, played end to end
 * inside the voice ring.
 */
export default function Call() {
  const [mode, setMode] = useState<RingMode>("idle");

  return (
    <section
      id="call"
      className="relative flex min-h-[120svh] scroll-mt-10 flex-col items-center justify-center overflow-hidden bg-ink py-24 text-paper"
    >
      <div className="trace absolute left-6 top-16 text-ash sm:left-8">
        01 — THE CALL
      </div>
      <Cross className="absolute right-6 top-16 text-paper" />

      <div className="relative flex aspect-square w-[min(92vw,88svh,860px)] items-center justify-center">
        <VoiceRing
          mode={mode}
          className="absolute inset-0 h-full w-full text-silver"
        />
        <div className="relative z-10 px-14 sm:px-24">
          <ConversationPlayer onMode={setMode} />
        </div>
      </div>

      <p className="trace mt-6 max-w-md px-6 text-center leading-relaxed text-steel">
        LIVE TRANSCRIPT · REAL PIPELINE ORDER · NO PORTAL, NO HOLD MUSIC
      </p>
    </section>
  );
}
