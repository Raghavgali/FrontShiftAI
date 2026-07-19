import { motion } from "framer-motion";
import React from "react";

import { cn } from "@/lib/utils";

const STAGGER = 0.02;

/**
 * Adapted from Skiper UI — Skiper 58 (TextRoll), free tier.
 * https://skiper-ui.com/v1/skiper58 · Author: @gurvinder-singh02
 */
const TextRoll: React.FC<{
  children: string;
  className?: string;
  center?: boolean;
}> = ({ children, className, center = false }) => {
  return (
    <motion.span
      initial="initial"
      whileHover="hovered"
      className={cn("relative block overflow-hidden", className)}
    >
      <span className="block">
        {children.split("").map((l, i) => {
          const delay = center
            ? STAGGER * Math.abs(i - (children.length - 1) / 2)
            : STAGGER * i;
          return (
            <motion.span
              variants={{ initial: { y: 0 }, hovered: { y: "-110%" } }}
              transition={{ ease: "easeInOut", delay, duration: 0.25 }}
              className="inline-block whitespace-pre"
              key={i}
            >
              {l}
            </motion.span>
          );
        })}
      </span>
      <span className="absolute inset-0 block" aria-hidden>
        {children.split("").map((l, i) => {
          const delay = center
            ? STAGGER * Math.abs(i - (children.length - 1) / 2)
            : STAGGER * i;
          return (
            <motion.span
              variants={{ initial: { y: "110%" }, hovered: { y: 0 } }}
              transition={{ ease: "easeInOut", delay, duration: 0.25 }}
              className="inline-block whitespace-pre"
              key={i}
            >
              {l}
            </motion.span>
          );
        })}
      </span>
    </motion.span>
  );
};

export { TextRoll };
