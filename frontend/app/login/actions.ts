"use server";

import { randomBytes } from "node:crypto";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { assertSameOrigin, cookieName, encodeSession, verifyPassword } from "../../lib/auth";
import { LoginFailureLimiter } from "../../lib/login-rate-limit";
import { validateFrontendSecurityConfig } from "../../lib/security-config";

const loginFailures = new LoginFailureLimiter();

export async function login(form: FormData) {
  await assertSameOrigin();
  const username = String(form.get("username") ?? "");
  const password = String(form.get("password") ?? "");
  const rateIdentity = username === process.env.OPERATOR_USERNAME ? username : "__unknown__";

  if (loginFailures.blocked(rateIdentity)) redirect("/login?error=1");
  if (username !== process.env.OPERATOR_USERNAME || !verifyPassword(password)) {
    loginFailures.recordFailure(rateIdentity);
    redirect("/login?error=1");
  }

  loginFailures.clear(rateIdentity);
  const config = validateFrontendSecurityConfig();
  const maxAge = config.sessionMaxAgeSeconds;
  (await cookies()).set(
    cookieName,
    encodeSession({
      role: config.role,
      exp: Date.now() + maxAge * 1000,
      nonce: randomBytes(16).toString("hex"),
    }),
    {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "strict",
      path: "/",
      maxAge,
    },
  );
  redirect("/");
}
