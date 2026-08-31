/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  // Traces the minimal set of files the server actually needs into
  // .next/standalone, so the production image ships ~200MB of node_modules
  // less. Ignored by `next dev`, so local development is unaffected.
  output: "standalone",

  // The API is served from the same origin in production (Traefik routes
  // /api to the backend), which is what makes the session cookie
  // same-origin there. NEXT_PUBLIC_API_URL is left empty in prod so fetch
  // uses relative paths.
  poweredByHeader: false,
};

module.exports = nextConfig;
