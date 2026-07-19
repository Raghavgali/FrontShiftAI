# FrontShiftAI Landing (frontend_v2)

A story-driven landing page for FrontShiftAI, built so a visitor who never
signs in still understands exactly what the product is and how it works.
The page traces one night-shift nurse's 2 a.m. question end to end: the live
call, the pipeline behind it, the agents, the tenant isolation guarantees,
and the engineering receipts.

Built separately from the existing app in `frontend/`, which is untouched.

## Stack

- Vite + React 18 + TypeScript
- Tailwind CSS v4
- Framer Motion + Lenis smooth scroll
- [Skiper UI](https://skiper-ui.com) free components, adapted in
  `src/components/ui/`: scroll character reveal (Skiper 31), pinned card
  stack (Skiper 16), expand-on-hover strip (Skiper 52), text roll nav
  (Skiper 58), animated counters (Skiper 37), progressive blur (Skiper 41)

## Design

- Concept: "engineered like equipment." Near-total monochrome, black hero
  into a silver film-grain body, with one hi-vis safety-orange accent used
  only for the worker's voice and live indicators.
- Type system with meaning: Archivo (expanded caps) for structure,
  Instrument Serif italic for human speech, IBM Plex Mono for system traces.
- Blueprint furniture: a fixed left chapter rail with scroll progress
  (01 The Call through 05 Proof), crosshair registration marks, and faint
  circular guides. The rail uses mix-blend-difference so it inverts itself
  over dark and light sections.
- Signature: the voice ring in chapter 01, a circular 128-tick audio meter
  that breathes while idle and swings with whoever holds the floor while a
  transcribed call plays inside it.
- Reduced motion is respected: the call renders as a static transcript and
  scroll animations settle to their final state.

## Run it

```bash
cd frontend_v2
npm install
npm run dev        # dev server
npm run build      # production build (tsc + vite)
npm run preview    # serve the production build
```

Screenshots of each section are in `screenshots/`.
