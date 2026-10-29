import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const extraHosts = (process.env.VITE_ALLOWED_HOSTS ?? "")
  .split(",")
  .map((host) => host.trim())
  .filter(Boolean);

// Port 3000 is often already taken by another dev server, and `strictPort`
// below turns that into a hard failure rather than a silent move. Override
// with VITE_PORT when 3001 is busy too.
const devPort = Number(process.env.VITE_PORT ?? 3001);

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: devPort,
    strictPort: true,
    allowedHosts: ["localhost", "127.0.0.1", ...extraHosts],
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
  preview: {
    host: "0.0.0.0",
    port: devPort,
    allowedHosts: ["localhost", "127.0.0.1", ...extraHosts],
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
});
