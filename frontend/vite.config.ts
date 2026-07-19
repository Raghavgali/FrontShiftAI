import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

export default defineConfig({
  // GitHub Pages serves the site from /FrontShiftAI/; Vercel and local
  // builds stay at the root.
  base: process.env.GITHUB_PAGES === "true" ? "/FrontShiftAI/" : "/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
