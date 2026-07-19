import { TextRoll } from "@/components/ui/text-roll";

const LINKS = [
  { label: "THE CALL", href: "#call" },
  { label: "PIPELINE", href: "#pipeline" },
  { label: "THE CREW", href: "#crew" },
  { label: "THE WALLS", href: "#walls" },
  { label: "PROOF", href: "#proof" },
];

export default function Nav() {
  return (
    <header className="fixed inset-x-0 top-0 z-50 mix-blend-difference">
      <nav className="flex items-center justify-between px-5 py-4 text-white sm:px-8">
        <a href="#top" className="voice text-[1.35rem] leading-none">
          FrontShiftAI
        </a>
        <div className="hidden items-center gap-7 md:flex">
          {LINKS.map((l) => (
            <a key={l.href} href={l.href} className="trace text-white/80 hover:text-white">
              <TextRoll>{l.label}</TextRoll>
            </a>
          ))}
        </div>
        <a
          href="https://github.com/Raghavgali/FrontShiftAI"
          target="_blank"
          rel="noreferrer"
          className="trace rounded-full border border-white/30 px-4 py-2 text-white transition-colors hover:border-white"
        >
          GITHUB ↗
        </a>
      </nav>
    </header>
  );
}
