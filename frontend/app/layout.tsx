import "./globals.css";
import { Navigation } from "@/components/navigation";
export const metadata = { title: "Quant Lab · Investiční laboratoř", description: "Přehled strategií, simulovaného portfolia a provozu" };
export default function Layout({ children }: {
    children: React.ReactNode;
}) { return <html lang="cs"><body><a className="skip-link" href="#content">Přejít na obsah</a><header><div className="brand"><span className="brand-icon" aria-hidden="true">Q</span><div><strong>Quant Lab</strong><small>Investiční laboratoř</small></div></div><span className="badge safe">Simulační režim · Bez skutečných objednávek</span></header><div className="shell"><Navigation /><main id="content">{children}</main></div><footer>Quant Lab · Simulované obchodování · Časy záznamů jsou uváděny v UTC.</footer></body></html>; }
