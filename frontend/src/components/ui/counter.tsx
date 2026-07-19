import { animate, useInView, useReducedMotion } from "framer-motion";
import React, { useEffect, useRef } from "react";

/**
 * Count-up number, adapted from Skiper UI — Skiper 37 (AnimatedNumber)
 * counting patterns, free tier, without the NumberFlow dependency.
 * https://skiper-ui.com/v1/skiper37 · Author: @gurvinder-singh02
 */
const Counter = ({
  value,
  decimals = 0,
  className,
}: {
  value: number;
  decimals?: number;
  className?: string;
}) => {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, margin: "-80px" });
  const reduce = useReducedMotion();

  useEffect(() => {
    if (!inView || !ref.current) return;
    if (reduce) {
      ref.current.textContent = value.toFixed(decimals);
      return;
    }
    const controls = animate(0, value, {
      duration: 1.6,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: (v) => {
        if (ref.current) ref.current.textContent = v.toFixed(decimals);
      },
    });
    return () => controls.stop();
  }, [inView, value, decimals, reduce]);

  return (
    <span ref={ref} className={className}>
      0
    </span>
  );
};

export { Counter };
