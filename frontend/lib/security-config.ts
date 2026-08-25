export type Role = "VIEWER" | "OPERATOR" | "ADMIN";

export type FrontendSecurityConfig = {
  role: Role;
  sessionMaxAgeSeconds: number;
};

const roles = new Set<Role>(["VIEWER", "OPERATOR", "ADMIN"]);
const forbiddenSecretFragments = ["changeme", "placeholder"];

function requireStrongSecret(name: string): string {
  const value = process.env[name] ?? "";
  const lowered = value.toLowerCase();
  if (
    value.length < 43 ||
    forbiddenSecretFragments.some((fragment) => lowered.includes(fragment))
  ) {
    throw new Error(`${name} musí být unikátní secret s alespoň 256 bity entropie`);
  }
  return value;
}

export function validateFrontendSecurityConfig(): FrontendSecurityConfig {
  const sessionSecret = requireStrongSecret("SESSION_SECRET");
  const tokens = [
    requireStrongSecret("QUANTLAB_API_VIEWER_TOKEN"),
    requireStrongSecret("QUANTLAB_API_OPERATOR_TOKEN"),
    requireStrongSecret("QUANTLAB_API_ADMIN_TOKEN"),
  ];
  if (new Set([sessionSecret, ...tokens]).size !== 4) {
    throw new Error("Frontend security secrets musí být navzájem unikátní");
  }
  if (!(process.env.OPERATOR_USERNAME ?? "").trim()) {
    throw new Error("OPERATOR_USERNAME je povinný");
  }
  const passwordHash = process.env.OPERATOR_PASSWORD_SCRYPT ?? "";
  if (!/^[A-Za-z0-9_-]{22}:[A-Za-z0-9_-]{43}$/.test(passwordHash)) {
    throw new Error("OPERATOR_PASSWORD_SCRYPT nemá platný scrypt formát");
  }
  const role = process.env.OPERATOR_ROLE as Role;
  if (!roles.has(role)) {
    throw new Error("OPERATOR_ROLE musí být VIEWER, OPERATOR nebo ADMIN");
  }
  const sessionMaxAgeSeconds = Number(process.env.SESSION_MAX_AGE_SECONDS);
  if (!Number.isInteger(sessionMaxAgeSeconds) || sessionMaxAgeSeconds < 300 || sessionMaxAgeSeconds > 86400) {
    throw new Error("SESSION_MAX_AGE_SECONDS musí být celé číslo od 300 do 86400");
  }
  const publicBaseUrl = new URL(process.env.PUBLIC_BASE_URL ?? "");
  const production = process.env.NODE_ENV === "production";
  const insecureDevelopmentUrl =
    !production &&
    publicBaseUrl.protocol === "http:" &&
    ["localhost", "127.0.0.1"].includes(publicBaseUrl.hostname);
  if (
    (publicBaseUrl.protocol !== "https:" && !insecureDevelopmentUrl) ||
    publicBaseUrl.username ||
    publicBaseUrl.password
  ) {
    throw new Error("Production PUBLIC_BASE_URL musí být bezpečná HTTPS URL");
  }
  const hosts = (process.env.FRONTEND_ALLOWED_HOSTS ?? "").split(",").map((host) => host.trim());
  if (!hosts.includes(publicBaseUrl.hostname) || hosts.includes("*")) {
    throw new Error("FRONTEND_ALLOWED_HOSTS musí explicitně obsahovat PUBLIC_BASE_URL host");
  }
  return {role, sessionMaxAgeSeconds};
}
