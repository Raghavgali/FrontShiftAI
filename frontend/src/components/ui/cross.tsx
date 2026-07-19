import { cn } from "@/lib/utils";

/* Blueprint registration mark. */
const Cross = ({ className }: { className?: string }) => (
  <svg
    viewBox="0 0 24 24"
    aria-hidden
    className={cn("h-5 w-5 opacity-30", className)}
  >
    <path d="M12 2v20M2 12h20" stroke="currentColor" strokeWidth="0.75" />
  </svg>
);

export { Cross };
