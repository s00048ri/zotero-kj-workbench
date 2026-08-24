import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// One process in production: the build lands where FastAPI serves it from.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../src/zkj/api/web/dist", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8420" } },
});
