import type { NextConfig } from "next";

const csp = "default-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'";
const securityHeaders = [{key:"Content-Security-Policy",value:csp},{key:"X-Content-Type-Options",value:"nosniff"},{key:"Referrer-Policy",value:"no-referrer"},{key:"Permissions-Policy",value:"camera=(), microphone=(), geolocation=()"},{key:"X-Frame-Options",value:"DENY"},{key:"Cache-Control",value:"no-store"}];
if (process.env.NODE_ENV === "production" && process.env.PUBLIC_BASE_URL?.startsWith("https://")) securityHeaders.push({key:"Strict-Transport-Security",value:"max-age=31536000; includeSubDomains"});

export function codespacesAllowedOrigins(environment: Readonly<Record<string, string | undefined>>): string[] | undefined {
  if (environment.CODESPACES !== "true" || !environment.CODESPACE_NAME || !environment.GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN) return undefined;
  const host = `${environment.CODESPACE_NAME}-3000.${environment.GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}`;
  if (!/^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$/.test(host) || host.includes("..")) throw new Error("Neplatný GitHub Codespaces host pro Server Actions");
  return [host, "localhost:3000", "127.0.0.1:3000"];
}

const allowedOrigins = codespacesAllowedOrigins(process.env);
const config: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  ...(allowedOrigins ? {experimental: {serverActions: {allowedOrigins}}} : {}),
  async headers() { return [{source:"/:path*",headers:securityHeaders}]; },
};
export default config;
