import {validateFrontendSecurityConfig} from "./lib/security-config";

export function register() {
  if (
    process.env.NEXT_RUNTIME === "nodejs" &&
    process.env.NODE_ENV === "production" &&
    process.env.VALIDATE_PRODUCTION_STARTUP === "true"
  ) {
    validateFrontendSecurityConfig();
  }
}
