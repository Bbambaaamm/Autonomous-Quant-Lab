"use server";
import {randomBytes} from "node:crypto";
import {cookies} from "next/headers";
import {redirect} from "next/navigation";
import {assertSameOrigin,cookieName,encodeSession,verifyPassword} from "../../lib/auth";
const attempts=new Map<string,number[]>();
function allowed(identity:string){const now=Date.now();const key=identity.slice(0,128);const recent=(attempts.get(key)??[]).filter(value=>value>now-300_000);if(recent.length>=5)return false;recent.push(now);attempts.set(key,recent);return true}

export async function login(form:FormData){await assertSameOrigin();const username=String(form.get("username")??"");const password=String(form.get("password")??"");if(!allowed(username)||username!==process.env.OPERATOR_USERNAME||!verifyPassword(password))redirect("/login?error=1");const role=(process.env.OPERATOR_ROLE??"ADMIN") as "VIEWER"|"OPERATOR"|"ADMIN";const maxAge=Number(process.env.SESSION_MAX_AGE_SECONDS??3600);(await cookies()).set(cookieName,encodeSession({role,exp:Date.now()+maxAge*1000,nonce:randomBytes(16).toString("hex")}),{httpOnly:true,secure:process.env.NODE_ENV==="production",sameSite:"strict",path:"/",maxAge});redirect("/")}
