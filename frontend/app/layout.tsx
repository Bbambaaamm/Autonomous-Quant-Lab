import "./globals.css";import Link from "next/link";
const links=[["/","Přehled"],["/paper","Paper"],["/strategies","Strategie"],["/research","Research"],["/risk","Risk"],["/data","Data"],["/operations","Operations"],["/audit","Audit"]];
export const metadata={title:"Quant Lab · Operator",description:"Lokální paper-only operator control plane"};
export default function Layout({children}:{children:React.ReactNode}){return <html lang="cs"><body><header><strong>Autonomous Quant Lab</strong><span className="badge safe">REŽIM: PAPER · LIVE ABSENT</span></header><div className="shell"><nav aria-label="Hlavní navigace">{links.map(([h,t])=><Link key={h} href={h}>{t}</Link>)}</nav><main>{children}</main></div></body></html>}
