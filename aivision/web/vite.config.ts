import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// 개발 시에는 Vite 가 5173 에서 뜨고 /api 와 /ws 는 FastAPI(8000)로 넘긴다.
// 운영 빌드는 dist/ 를 FastAPI 가 그대로 서빙하므로 프록시가 필요 없다(단일 오리진).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
});
