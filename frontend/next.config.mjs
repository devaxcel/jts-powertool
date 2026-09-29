/** @type {import('next').NextConfig} */
const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:8000";

const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${BACKEND_URL}/api/:path*`,
      },
      {
        source: "/logs/stream",
        destination: `${BACKEND_URL}/logs/stream`,
      },
      {
        source: "/logs/api/:path*",
        destination: `${BACKEND_URL}/logs/api/:path*`,
      },
      {
        source: "/context/api/:path*",
        destination: `${BACKEND_URL}/context/api/:path*`,
      },
      {
        source: "/database/api/:path*",
        destination: `${BACKEND_URL}/database/api/:path*`,
      },
    ];
  },
};

export default nextConfig;

