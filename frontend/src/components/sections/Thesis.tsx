import { CharRevealSection } from "@/components/ui/char-reveal";

/**
 * The black-to-silver turn of the page, and the one-line thesis that
 * assembles as you scroll (Skiper 31 pattern).
 */
export default function Thesis() {
  return (
    <>
      <div className="h-[34svh] bg-gradient-to-b from-ink via-graphite to-silver" />
      <section className="bg-silver text-ink">
        <CharRevealSection text="ASK. IT ANSWERS. IT ACTS.">
          <p className="max-w-2xl text-center text-base leading-relaxed text-steel sm:text-lg">
            Nineteen companies, one deployment. Any worker can ask about the
            handbook, check a balance, book time off, or open a ticket — and
            get an answer that cites the page it came from. That call you just
            heard is the whole product. The rest of this page is how it works.
          </p>
        </CharRevealSection>
      </section>
    </>
  );
}
