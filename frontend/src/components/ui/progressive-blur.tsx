import React from "react";

/**
 * Adapted from Skiper UI — Skiper 41 (ProgressiveBlur), free tier.
 * https://skiper-ui.com/v1/skiper41 · Author: @gurvinder-singh02
 */
type ProgressiveBlurProps = {
  className?: string;
  backgroundColor?: string;
  position?: "top" | "bottom";
  height?: string;
  blurAmount?: string;
};

const ProgressiveBlur = ({
  className = "",
  backgroundColor = "#0a0a0b",
  position = "top",
  height = "120px",
  blurAmount = "6px",
}: ProgressiveBlurProps) => {
  const isTop = position === "top";
  return (
    <div
      className={`pointer-events-none absolute left-0 w-full select-none ${className}`}
      style={{
        [isTop ? "top" : "bottom"]: 0,
        height,
        background: isTop
          ? `linear-gradient(to top, transparent, ${backgroundColor})`
          : `linear-gradient(to bottom, transparent, ${backgroundColor})`,
        maskImage: isTop
          ? `linear-gradient(to bottom, ${backgroundColor} 50%, transparent)`
          : `linear-gradient(to top, ${backgroundColor} 50%, transparent)`,
        WebkitBackdropFilter: `blur(${blurAmount})`,
        backdropFilter: `blur(${blurAmount})`,
      }}
    />
  );
};

export { ProgressiveBlur };
