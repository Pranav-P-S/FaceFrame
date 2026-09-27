import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// Electron loads the built index.html straight from disk, so asset URLs
// must be relative or they resolve against the filesystem root.
function cspForProduction(): Plugin {
  return {
    name: "csp-for-production",
    apply: "build",
    transformIndexHtml(html) {
      const csp =
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' media:; connect-src 'self'";
      return html.replace(
        "<head>",
        `<head>\n    <meta http-equiv="Content-Security-Policy" content="${csp}" />`
      );
    },
  };
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), cspForProduction()],
  base: "./",
  server: {
    port: 1420,
    strictPort: true,
  },
  clearScreen: false,
  build: {
    rollupOptions: {
      output: {
        // Third-party code (React, Zustand, …) changes far less often than
        // the app bundle; splitting keeps the app chunk small so V8 parses
        // less on startup.
        manualChunks(id) {
          if (id.includes("node_modules")) return "vendor";
          return undefined;
        },
      },
    },
  },
});
