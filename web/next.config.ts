import type { NextConfig } from "next";

const config: NextConfig = {
  // node:sqlite is a Node built-in; keep it out of the bundle.
  serverExternalPackages: ["node:sqlite"],
};

export default config;
