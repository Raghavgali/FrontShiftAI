import { motion, useScroll } from "framer-motion";
import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

const CHAPTERS = [
  { id: "call", n: "01", label: "THE CALL" },
  { id: "pipeline", n: "02", label: "PIPELINE" },
  { id: "crew", n: "03", label: "THE CREW" },
  { id: "walls", n: "04", label: "THE WALLS" },
  { id: "proof", n: "05", label: "PROOF" },
];

/**
 * Fixed chapter index, lightweight.info style: the page is one request
 * traced in order, so the rail is a table of contents with a progress line.
 */
export default function Rail() {
  const { scrollYProgress } = useScroll();
  const [active, setActive] = useState<string>("");

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) setActive(e.target.id);
        });
      },
      { rootMargin: "-40% 0px -50% 0px" }
    );
    CHAPTERS.forEach((c) => {
      const el = document.getElementById(c.id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, []);

  return (
    <aside className="fixed left-6 top-1/2 z-40 hidden -translate-y-1/2 mix-blend-difference lg:block">
      <div className="relative flex flex-col gap-9 pl-4 text-white">
        <div className="absolute bottom-1 left-0 top-1 w-px bg-white/20" />
        <motion.div
          className="absolute left-0 top-1 w-px origin-top bg-white"
          style={{ scaleY: scrollYProgress, height: "calc(100% - 8px)" }}
        />
        {CHAPTERS.map((c) => (
          <a
            key={c.id}
            href={`#${c.id}`}
            className={cn(
              "trace flex flex-col gap-0.5 transition-opacity",
              active === c.id ? "opacity-100" : "opacity-35 hover:opacity-70"
            )}
          >
            <span>{c.n}</span>
            <span>{c.label}</span>
          </a>
        ))}
      </div>
    </aside>
  );
}
