import type { NextConfig } from "next";

const config: NextConfig = {
  // Self-contained server for the Docker image (ignored by Vercel).
  output: "standalone",
  // node:sqlite is a Node built-in; keep it out of the bundle.
  serverExternalPackages: ["node:sqlite"],
};

export default config;
