import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  rewrites() {
    const backend = (process.env.CFO_BACKEND_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
