import { Cross } from "@/components/ui/cross";

const WALLS = [
  {
    n: "W-01",
    title: "FENCED BY DEFAULT",
    body: "Every database query gets your company's filter added automatically. If the filter cannot be applied, the query refuses to run at all.",
    receipt: "FAIL CLOSED, NOT OPEN — TENANT_CONTEXT.PY",
  },
  {
    n: "W-02",
    title: "NEVER TWICE",
    body: "Retry a booking after a timeout and you get the same booking back, not a second one. The key is reserved before the work begins.",
    receipt: "RESERVE → EXECUTE → STORE",
  },
  {
    n: "W-03",
    title: "STOLEN TOKENS DIE FAST",
    body: "Refresh tokens rotate on every use. Replay an old one and the whole chain is burned — the thief and the session both lose.",
    receipt: "ONE ATOMIC UPDATE CLAIMS THE TOKEN",
  },
];

/**
 * Chapter 04 — tenant isolation, stated twice: once for the worker,
 * once in the engineering voice for whoever opens the code.
 */
export default function Walls() {
  return (
    <section
      id="walls"
      className="relative scroll-mt-10 overflow-hidden bg-ink px-6 py-32 text-paper"
    >
      {/* blueprint guides */}
      <svg
        aria-hidden
        className="pointer-events-none absolute -right-64 -top-64 h-[52rem] w-[52rem] text-white"
        viewBox="0 0 100 100"
      >
        <circle cx="50" cy="50" r="49" fill="none" stroke="currentColor" strokeOpacity="0.06" strokeWidth="0.2" />
        <circle cx="50" cy="50" r="34" fill="none" stroke="currentColor" strokeOpacity="0.05" strokeWidth="0.2" />
      </svg>
      <Cross className="absolute left-6 top-16 text-paper" />

      <div className="mx-auto max-w-6xl">
        <p className="trace text-ash">04 — THE WALLS</p>
        <h2 className="display mt-6 max-w-4xl text-[clamp(2.2rem,6vw,4.8rem)]">
          Nineteen companies. One deployment. Zero bleed.
        </h2>
        <p className="mt-6 max-w-xl text-base leading-relaxed text-ash">
          Multi-tenancy is the load-bearing wall of this system, so it is
          engineered like one. Each guarantee below is enforced in code and
          pinned by regression tests in the CI suite.
        </p>

        <div className="mt-16 grid gap-px border-y border-white/10 md:grid-cols-3 md:divide-x md:divide-white/10">
          {WALLS.map((w) => (
            <div key={w.n} className="flex flex-col gap-5 py-10 md:px-8 md:first:pl-0 md:last:pr-0">
              <span className="trace text-steel">{w.n}</span>
              <h3 className="display text-2xl">{w.title}</h3>
              <p className="text-sm leading-relaxed text-ash">{w.body}</p>
              <p className="trace mt-auto border-l-2 border-hivis pl-4 leading-relaxed text-silver">
                {w.receipt}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
