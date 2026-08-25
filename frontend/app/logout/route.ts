import {NextResponse} from "next/server";
import {cookieName} from "../../lib/auth";
export async function POST(request:Request){const origin=request.headers.get("origin");const host=request.headers.get("host");if(!origin||!host||new URL(origin).host!==host)return NextResponse.json({detail:"CSRF_REJECTED"},{status:403});const response=NextResponse.redirect(new URL("/login",process.env.PUBLIC_BASE_URL??"http://localhost:3000"),303);response.cookies.set(cookieName,"",{httpOnly:true,secure:process.env.NODE_ENV==="production",sameSite:"strict",path:"/",maxAge:0});return response}
