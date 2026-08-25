import type { NextConfig } from "next";
const csp="default-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'";
const securityHeaders=[{key:"Content-Security-Policy",value:csp},{key:"X-Content-Type-Options",value:"nosniff"},{key:"Referrer-Policy",value:"no-referrer"},{key:"Permissions-Policy",value:"camera=(), microphone=(), geolocation=()"},{key:"X-Frame-Options",value:"DENY"},{key:"Cache-Control",value:"no-store"}];
if(process.env.NODE_ENV==="production")securityHeaders.push({key:"Strict-Transport-Security",value:"max-age=31536000; includeSubDomains"});
const config: NextConfig = {output:"standalone",poweredByHeader:false,async headers(){return [{source:"/:path*",headers:securityHeaders}]}};
export default config;
