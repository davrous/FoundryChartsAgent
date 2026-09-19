import { defineConfig } from "vite";

export default defineConfig({
  build: { target: "es2022", outDir: "dist", emptyOutDir: true },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8190",
      "/health": "http://127.0.0.1:8190",
    },
  },
});
