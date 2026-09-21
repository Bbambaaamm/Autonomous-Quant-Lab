"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
const links = [["/", "Přehled", "01"], ["/paper", "Simulované portfolio", "02"], ["/strategies", "Strategie", "03"], ["/research", "Výzkum", "04"], ["/risk", "Řízení rizik", "05"], ["/data", "Tržní data", "06"], ["/operations", "Provoz", "07"], ["/audit", "Historie událostí", "08"]];
export function Navigation() { const path = usePathname(); return <nav aria-label="Hlavní navigace"><p className="nav-caption">PRACOVNÍ PROSTOR</p>{links.map(([href, text, n]) => <Link key={href} href={href} aria-current={(href === "/" ? path === href : path.startsWith(href)) ? "page" : undefined}><span className="nav-index" aria-hidden="true">{n}</span>{text}</Link>)}<p className="nav-note">Simulované obchodování<br />Bez skutečných objednávek</p></nav>; }
