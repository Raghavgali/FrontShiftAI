import { useReducedMotion } from "framer-motion";
import { useEffect, useRef } from "react";

export type RingMode = "idle" | "worker" | "agent";

const BARS = 128;
const INNER = 0.72; // inner radius as a fraction of half-size

function barPattern(i: number, t: number) {
  return (
    Math.abs(
      Math.sin(i * 0.61 + t * 1.9) * 0.5 +
        Math.sin(i * 1.7 - t * 1.3) * 0.3 +
        Math.sin(i * 0.23 + t * 0.7) * 0.2
    )
  );
}

/**
 * The instrument at the center of the page: a circular voice meter.
 * 128 radial ticks breathe while nobody speaks, and swing to the
 * amplitude of whoever holds the floor. The worker's turns are marked
 * by the single hi-vis arc segment.
 */
export default function VoiceRing({
  mode,
  className,
}: {
  mode: RingMode;
  className?: string;
}) {
  const pathRef = useRef<SVGPathElement>(null);
  const accentRef = useRef<SVGPathElement>(null);
  const modeRef = useRef<RingMode>(mode);
  const reduce = useReducedMotion();
  modeRef.current = mode;

  useEffect(() => {
    const el = pathRef.current;
    const accent = accentRef.current;
    if (!el || !accent) return;

    const S = 1000;
    const c = S / 2;
    const rIn = c * INNER;
    let amp = 8;
    let raf = 0;
    let start = performance.now();

    const draw = (now: number) => {
      const t = (now - start) / 1000;
      const target = modeRef.current === "idle" ? 9 : 46;
      amp += (target - amp) * 0.06;

      let d = "";
      let dAccent = "";
      for (let i = 0; i < BARS; i++) {
        const angle = (i / BARS) * Math.PI * 2 - Math.PI / 2 + t * 0.02;
        const len = 5 + barPattern(i, t) * amp;
        const x1 = c + Math.cos(angle) * rIn;
        const y1 = c + Math.sin(angle) * rIn;
        const x2 = c + Math.cos(angle) * (rIn + len);
        const y2 = c + Math.sin(angle) * (rIn + len);
        const seg = `M${x1.toFixed(1)} ${y1.toFixed(1)}L${x2.toFixed(1)} ${y2.toFixed(1)}`;
        // the worker's segment of the dial: a fixed 20-bar arc
        if (modeRef.current === "worker" && i >= 4 && i < 24) dAccent += seg;
        else d += seg;
      }
      el.setAttribute("d", d);
      accent.setAttribute("d", dAccent);
      raf = requestAnimationFrame(draw);
    };

    if (reduce) {
      // one static frame
      draw(start + 500);
      cancelAnimationFrame(raf);
      return;
    }
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [reduce]);

  return (
    <svg viewBox="0 0 1000 1000" aria-hidden className={className}>
      {/* engineering guide circles */}
      <circle cx="500" cy="500" r="475" fill="none" stroke="currentColor" strokeOpacity="0.10" strokeWidth="1" />
      <circle cx="500" cy="500" r="330" fill="none" stroke="currentColor" strokeOpacity="0.07" strokeWidth="1" />
      <path ref={pathRef} stroke="currentColor" strokeOpacity="0.55" strokeWidth="2" fill="none" />
      <path ref={accentRef} stroke="#ff4d00" strokeWidth="2.5" fill="none" />
    </svg>
  );
}
