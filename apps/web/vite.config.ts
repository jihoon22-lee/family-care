import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "prompt",
      includeAssets: ["icon.svg"],
      manifest: {
        name: "FamilyCare",
        short_name: "FamilyCare",
        description:
          "가족 보험의 가입 담보와 약관 근거를 연결하는 개인용 안내 도구",
        theme_color: "#10233f",
        background_color: "#eff5f8",
        display: "standalone",
        start_url: "/",
        icons: [
          {
            src: "/icon.svg",
            sizes: "any",
            type: "image/svg+xml",
            purpose: "any maskable",
          },
        ],
      },
      workbox: {
        // API responses and documents require a separately approved cache-policy change.
        globPatterns: ["**/*.{css,html,js,svg}"],
        navigateFallbackDenylist: [
          /^\/api\//,
          /^\/documents\//,
          /^\/evidence\//,
          /^\/medical-events\//,
          /^\/results\//,
          /^\/claims\//,
        ],
      },
    }),
  ],
  test: {
    environment: "jsdom",
    exclude: ["e2e/**", "node_modules/**", "dist/**"],
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
