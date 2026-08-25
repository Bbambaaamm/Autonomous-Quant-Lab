import "server-only";
import {cookies, headers} from "next/headers";
import {createHmac, scryptSync, timingSafeEqual} from "node:crypto";

export type Role="VIEWER"|"OPERATOR"|"ADMIN";
type Session={role:Role;exp:number;nonce:string};
export const cookieName=process.env.NODE_ENV==="production"?"__Host-quantlab_session":"quantlab_session";

function secret():string{const value=process.env.SESSION_SECRET??"";if(value.length<43)throw new Error("SESSION_SECRET musí mít alespoň 256 bitů");return value}
function sign(payload:string){return createHmac("sha256",secret()).update(payload).digest("base64url")}
export function encodeSession(session:Session){const payload=Buffer.from(JSON.stringify(session)).toString("base64url");return `${payload}.${sign(payload)}`}
export function decodeSession(value:string|undefined):Session|null{if(!value)return null;const [payload,mac,...rest]=value.split(".");if(!payload||!mac||rest.length)return null;const expected=Buffer.from(sign(payload));const actual=Buffer.from(mac);if(expected.length!==actual.length||!timingSafeEqual(expected,actual))return null;try{const parsed=JSON.parse(Buffer.from(payload,"base64url").toString()) as Session;if(!["VIEWER","OPERATOR","ADMIN"].includes(parsed.role)||parsed.exp<=Date.now())return null;return parsed}catch{return null}}
export async function session(){return decodeSession((await cookies()).get(cookieName)?.value)}
export async function requireSession(){const value=await session();if(!value)throw new Error("AUTH_REQUIRED");return value}
export function verifyPassword(password:string){const stored=process.env.OPERATOR_PASSWORD_SCRYPT??"";const [salt,hash,...rest]=stored.split(":");if(!salt||!hash||rest.length||password.length>1024)return false;const expected=Buffer.from(hash,"base64url");const actual=scryptSync(password,Buffer.from(salt,"base64url"),expected.length);return actual.length===expected.length&&timingSafeEqual(actual,expected)}
export async function assertSameOrigin(){const h=await headers();const origin=h.get("origin");const host=h.get("host");if(!origin||!host){throw new Error("CSRF_REJECTED")}const parsed=new URL(origin);if(parsed.host!==host)throw new Error("CSRF_REJECTED")}
export function backendToken(role:Role){const key=`QUANTLAB_API_${role}_TOKEN`;const value=process.env[key];if(!value)throw new Error(`Chybí server-only ${key}`);return value}
