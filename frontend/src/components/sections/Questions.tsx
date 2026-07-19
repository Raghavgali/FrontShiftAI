const ITEMS = [
  {
    q: "What is FrontShiftAI, in one sentence?",
    a: "A multi-tenant AI concierge that lets deskless workers ask HR anything by voice or chat, and get handbook-cited answers, PTO bookings, and HR tickets without ever finding a desktop portal.",
  },
  {
    q: "Which models does it run on?",
    a: "Voice and routing run on Groq (Llama 3.1 8B Instant) for speed. Chat answers default to Mercury, with Groq and OpenAI as automatic fallbacks — if one provider is down, the request falls through the chain instead of failing.",
  },
  {
    q: "How is nineteen companies' data kept separate?",
    a: "Three layers: document search is scoped to each company's vector collection, every database query gets a tenant filter added automatically and refuses to run without it, and cross-tenant access requires an explicitly audited bypass.",
  },
  {
    q: "Is this a finished product?",
    a: "It is a working system in active hardening. Eight of thirteen roadmap phases are shipped; the rest are public in plan.md, and performance targets stay labeled as targets until benchmarks are recorded.",
  },
  {
    q: "Who built it?",
    a: "It began as a Northeastern University team capstone. This continuation is individually directed by Raghav Gali — architecture, integration, testing, and review — with AI coding tools accelerating the implementation, credited transparently in the commit history.",
  },
];

export default function Questions() {
  return (
    <section className="border-t hairline-dark bg-silver px-6 py-28 text-ink">
      <div className="mx-auto max-w-3xl">
        <p className="trace text-steel">APPENDIX — QUESTIONS</p>
        <h2 className="display mt-6 text-[clamp(1.8rem,4.5vw,3.2rem)]">
          Asked before trusting it
        </h2>

        <div className="mt-12 border-t hairline-dark">
          {ITEMS.map((item, i) => (
            <details key={i} className="group border-b hairline-dark">
              <summary className="flex cursor-pointer list-none items-baseline justify-between gap-6 py-6 [&::-webkit-details-marker]:hidden">
                <span className="flex items-baseline gap-5">
                  <span className="trace text-steel">
                    Q{String(i + 1).padStart(2, "0")}
                  </span>
                  <span className="text-base font-medium sm:text-lg">
                    {item.q}
                  </span>
                </span>
                <span className="trace shrink-0 transition-transform duration-200 group-open:rotate-45">
                  +
                </span>
              </summary>
              <p className="pb-7 pl-12 pr-8 text-sm leading-relaxed text-steel sm:text-base">
                {item.a}
              </p>
            </details>
          ))}
        </div>
      </div>
    </section>
  );
}
