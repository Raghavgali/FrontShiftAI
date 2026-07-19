const REPO = "https://github.com/Raghavgali/FrontShiftAI";

export default function Footer() {
  return (
    <footer className="relative overflow-hidden bg-ink px-6 pb-0 pt-28 text-paper">
      <div className="mx-auto flex max-w-6xl flex-col gap-16">
        <div className="flex flex-col gap-8 md:flex-row md:items-end md:justify-between">
          <div>
            <p className="trace text-steel">06:00 — SHIFT CHANGE</p>
            <p className="voice mt-6 max-w-xl text-4xl leading-tight text-silver sm:text-5xl">
              Maya got her answer, booked her days, and went back to her
              patients.
            </p>
          </div>
          <div className="trace flex flex-col gap-3 text-ash">
            <a href={REPO} target="_blank" rel="noreferrer" className="hover:text-paper">
              GITHUB ↗
            </a>
            <a href={`${REPO}/blob/main/plan.md`} target="_blank" rel="noreferrer" className="hover:text-paper">
              THE PLAN ↗
            </a>
            <a href={`${REPO}#readme`} target="_blank" rel="noreferrer" className="hover:text-paper">
              README ↗
            </a>
          </div>
        </div>

        <div className="trace flex flex-col gap-2 border-t border-white/10 pt-6 text-steel">
          <span>
            AN INDIVIDUALLY DIRECTED, AI-ASSISTED CONTINUATION OF A NORTHEASTERN
            UNIVERSITY TEAM CAPSTONE · BY RAGHAV GALI
          </span>
          <span>
            TYPE: ARCHIVO / INSTRUMENT SERIF / IBM PLEX MONO · MOTION BUILT WITH{" "}
            <a
              href="https://skiper-ui.com"
              target="_blank"
              rel="noreferrer"
              className="underline decoration-white/30 underline-offset-4 hover:text-paper"
            >
              SKIPER UI
            </a>{" "}
            COMPONENTS
          </span>
        </div>
      </div>

      <div
        aria-hidden
        className="display pointer-events-none mt-14 select-none overflow-hidden whitespace-nowrap text-center text-[11vw] leading-[0.72] tracking-tight text-white/[0.05]"
      >
        FRONTSHIFT
      </div>
    </footer>
  );
}
