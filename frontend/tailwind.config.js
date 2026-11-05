/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        command: {
          bg: '#07090e',
          panel: '#0c1017',
          card: '#121822',
          border: '#1e293b',
          borderLight: '#334155',
          text: '#e2e8f0',
          muted: '#94a3b8',
          dim: '#64748b',
          accent: '#00f0ff',
          neonGreen: '#00ff88',
          neonAmber: '#ffaa00',
          neonRed: '#ff3366',
          neonPurple: '#a855f7',
        }
      },
      fontFamily: {
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
        sans: ['system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
