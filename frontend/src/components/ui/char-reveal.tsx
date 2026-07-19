import { motion, useReducedMotion, useScroll, useTransform, type MotionValue } from "framer-motion";
import React, { useRef } from "react";

import { cn } from "@/lib/utils";

/**
 * Adapted from Skiper UI — Skiper 31 (ScrollAnimation, CharacterV1), free tier.
 * https://skiper-ui.com/v1/skiper31 · Author: @gurvinder-singh02
 */
const Char = ({
  char,
  index,
  centerIndex,
  progress,
}: {
  char: string;
  index: number;
  centerIndex: number;
  progress: MotionValue<number>;
}) => {
  const distance = index - centerIndex;
  const x = useTransform(progress, [0, 0.5], [distance * 46, 0]);
  const rotateX = useTransform(progress, [0, 0.5], [distance * 40, 0]);
  const opacity = useTransform(progress, [0, 0.35], [0.25, 1]);

  return (
    <motion.span
      className={cn("inline-block", char === " " && "w-[0.35em]")}
      style={{ x, rotateX, opacity }}
    >
      {char}
    </motion.span>
  );
};

/**
 * A tall scroll section whose centered headline assembles character by
 * character as the visitor scrolls through it.
 */
const CharRevealSection = ({
  text,
  className,
  children,
}: {
  text: string;
  className?: string;
  children?: React.ReactNode;
}) => {
  const ref = useRef<HTMLDivElement | null>(null);
  const reduce = useReducedMotion();
  const { scrollYProgress } = useScroll({ target: ref });
  const centerIndex = Math.floor(text.length / 2);

  // Words stay unbreakable; the per-character offsets are still computed
  // against the position in the whole line.
  let charIndex = 0;
  const words = text.split(" ").map((word) => {
    const start = charIndex;
    charIndex += word.length + 1;
    return { word, start };
  });

  return (
    <div ref={ref} className={cn("relative h-[180vh]", className)}>
      <div className="sticky top-0 flex h-screen flex-col items-center justify-center gap-10 overflow-hidden px-6">
        <h2
          className="display max-w-6xl text-center text-[clamp(2.4rem,7vw,6.5rem)]"
          style={{ perspective: "600px" }}
        >
          {reduce
            ? text
            : words.map(({ word, start }, w) => (
                <span key={w} className="inline-block whitespace-nowrap">
                  {word.split("").map((char, i) => (
                    <Char
                      key={i}
                      char={char}
                      index={start + i}
                      centerIndex={centerIndex}
                      progress={scrollYProgress}
                    />
                  ))}
                  {w < words.length - 1 && (
                    <span className="inline-block w-[0.3em]" />
                  )}
                </span>
              ))}
        </h2>
        {children}
      </div>
    </div>
  );
};

export { CharRevealSection };
