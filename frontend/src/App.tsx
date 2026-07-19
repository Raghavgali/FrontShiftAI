import { ReactLenis } from "lenis/react";

import Nav from "@/components/sections/Nav";
import Rail from "@/components/sections/Rail";
import Hero from "@/components/sections/Hero";
import Call from "@/components/sections/Call";
import Thesis from "@/components/sections/Thesis";
import Pipeline from "@/components/sections/Pipeline";
import Crew from "@/components/sections/Crew";
import Walls from "@/components/sections/Walls";
import Proof from "@/components/sections/Proof";
import Questions from "@/components/sections/Questions";
import Footer from "@/components/sections/Footer";

export default function App() {
  return (
    <ReactLenis root>
      <main className="grain min-h-screen bg-ink">
        <Nav />
        <Rail />
        <Hero />
        <Call />
        <Thesis />
        <Pipeline />
        <Crew />
        <Walls />
        <Proof />
        <Questions />
        <Footer />
      </main>
    </ReactLenis>
  );
}
