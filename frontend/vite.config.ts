import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev proxy: API + WS go to the backend on :8000. In prod, nginx does this.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
