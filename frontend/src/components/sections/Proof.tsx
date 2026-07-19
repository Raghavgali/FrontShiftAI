import { Counter } from "@/components/ui/counter";

const REPO = "https://github.com/Raghavgali/FrontShiftAI";

const STATS = [
  { value: 13, decimals: 0, suffix: "", label: "PHASES IN THE PUBLIC HARDENING PLAN" },
  { value: 8, decimals: 0, suffix: "", label: "PHASES SHIPPED SO FAR" },
  { value: 6, decimals: 0, suffix: "", label: "GRAFANA DASHBOARDS WATCHING IT RUN" },
  { value: 99.5, decimals: 1, suffix: "%", label: "AVAILABILITY SLO TARGET" },
];

const DOCS = [
  {
    label: "PLAN.MD",
    desc: "The 13-phase roadmap, written before the work and kept honest after it",
    href: `${REPO}/blob/main/plan.md`,
  },
  {
    label: "STRESS_TESTS/",
    desc: "Deterministic security races and latency suites, run in CI",
    href: `${REPO}/tree/main/stress_tests`,
  },
  {
    label: "DOCS/BENCHMARKS/",
    desc: "Recorded runs with date, commit, environment, and sample size — including the failed ones",
    href: `${REPO}/tree/main/docs/benchmarks`,
  },
];

/**
 * Chapter 05 — the receipts. Numbers stay labeled as targets until a
 * recorded benchmark says otherwise; that rule is part of the repo.
 */
export default function Proof() {
  return (
    <section id="proof" className="scroll-mt-10 bg-silver px-6 py-32 text-ink">
      <div className="mx-auto max-w-6xl">
        <p className="trace text-steel">05 — THE PROOF</p>
        <h2 className="display mt-6 max-w-4xl text-[clamp(2.2rem,6vw,4.8rem)]">
          Targets stay targets until they're measured
        </h2>
        <p className="mt-6 max-w-xl text-base leading-relaxed text-steel">
          Everything on this page is documented in the open: the plan, the
          tests, the dashboards, and the benchmark rules that keep the claims
          honest.
        </p>

        <div className="mt-16 grid gap-10 border-t hairline-dark pt-12 sm:grid-cols-2 lg:grid-cols-4">
          {STATS.map((s) => (
            <div key={s.label} className="flex flex-col gap-3">
              <span className="display text-6xl">
                <Counter value={s.value} decimals={s.decimals} />
                {s.suffix}
              </span>
              <span className="trace max-w-[16rem] leading-relaxed text-steel">
                {s.label}
              </span>
            </div>
          ))}
        </div>

        <div className="mt-20 border-t hairline-dark">
          {DOCS.map((d) => (
            <a
              key={d.label}
              href={d.href}
              target="_blank"
              rel="noreferrer"
              className="group flex flex-col gap-2 border-b hairline-dark py-6 transition-colors hover:bg-slv2 sm:flex-row sm:items-baseline sm:justify-between"
            >
              <span className="trace text-ink">{d.label}</span>
              <span className="flex items-baseline gap-6 text-sm text-steel">
                {d.desc}
                <span className="trace transition-transform group-hover:translate-x-1">
                  ↗
                </span>
              </span>
            </a>
          ))}
        </div>
      </div>
    </section>
  );
}
