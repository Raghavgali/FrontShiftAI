import { motion } from "framer-motion";

import { Cross } from "@/components/ui/cross";

const ease = [0.16, 1, 0.3, 1] as const;

export default function Hero() {
  return (
    <section
      id="top"
      className="relative flex min-h-svh flex-col items-center justify-center overflow-hidden bg-ink px-6 text-paper"
    >
      <Cross className="absolute left-6 top-20 text-paper" />
      <Cross className="absolute right-6 top-20 text-paper" />

      <motion.p
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.8, delay: 0.1 }}
        className="trace mb-8 text-ash"
      >
        NIGHT SHIFT · SOMEWHERE, IT IS 02:14
      </motion.p>

      <h1 className="text-center">
        <motion.span
          initial={{ opacity: 0, y: 26 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.9, delay: 0.25, ease }}
          className="display block text-[clamp(2.8rem,8.5vw,8rem)]"
        >
          The 2 A.M.
        </motion.span>
        <motion.span
          initial={{ opacity: 0, y: 26 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.9, delay: 0.4, ease }}
          className="display block text-[clamp(2.8rem,8.5vw,8rem)]"
        >
          Question,
        </motion.span>
        <motion.span
          initial={{ opacity: 0, y: 26 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.9, delay: 0.55, ease }}
          className="voice block text-[clamp(3rem,9vw,8.4rem)] leading-[1.05] text-silver"
        >
          answered.
        </motion.span>
      </h1>

      <motion.p
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.8, delay: 0.9 }}
        className="mt-8 max-w-md text-center text-sm leading-relaxed text-ash"
      >
        FrontShiftAI is an AI concierge for people who work on their feet.
        Handbook answers, time off, HR tickets — by voice or chat, while HR
        sleeps.
      </motion.p>

      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.8, delay: 1.05 }}
        className="mt-10 flex items-center gap-4"
      >
        <a
          href="#call"
          className="trace rounded-full bg-paper px-6 py-3 text-ink transition-colors hover:bg-white"
        >
          LISTEN IN
        </a>
        <a
          href="https://github.com/Raghavgali/FrontShiftAI"
          target="_blank"
          rel="noreferrer"
          className="trace rounded-full border border-white/25 px-6 py-3 text-paper transition-colors hover:border-white"
        >
          READ THE CODE ↗
        </a>
      </motion.div>

      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 1, delay: 1.4 }}
        className="absolute bottom-8 left-1/2 flex -translate-x-1/2 flex-col items-center gap-3"
      >
        <span className="trace text-steel">FOLLOW THE REQUEST</span>
        <span className="h-14 w-px bg-gradient-to-b from-transparent to-white/60" />
      </motion.div>
    </section>
  );
}
