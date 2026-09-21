/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  experimental: {
    optimizePackageImports: ["lucide-react", "react-icons", "recharts"],
  },
  // Old "Escalations" URLs (renamed to Human Support) keep working.
  async redirects() {
    return [
      { source: "/escalations", destination: "/human-support", permanent: true },
      { source: "/calling/escalations", destination: "/calling/human-support", permanent: true },
      { source: "/whatsapp/escalations", destination: "/whatsapp/human-support", permanent: true },
    ];
  },
};

export default nextConfig;
