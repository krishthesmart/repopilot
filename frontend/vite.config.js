import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: process.env.BACKEND_URL || "http://127.0.0.1:8123",
        changeOrigin: true,
      },
    },
  },
});
