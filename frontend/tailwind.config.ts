import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        surface: "#0a0a0a",
        accent: "#e63946",
        success: "#c9a84c",
      },
    },
  },
  plugins: [],
};

export default config;
