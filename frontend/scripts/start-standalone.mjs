import { cpSync, existsSync } from "node:fs";
import { join } from "node:path";

const standaloneDirectory = join(process.cwd(), ".next", "standalone");
const server = join(standaloneDirectory, "server.js");

if (!existsSync(server)) {
  throw new Error("Standalone build chybí; nejprve spusťte `make dashboard-build`");
}

cpSync(join(process.cwd(), ".next", "static"), join(standaloneDirectory, ".next", "static"), {
  recursive: true,
  force: true,
});
const publicDirectory = join(process.cwd(), "public");
if (existsSync(publicDirectory)) {
  cpSync(publicDirectory, join(standaloneDirectory, "public"), { recursive: true, force: true });
}

process.env.NODE_ENV = "production";
process.env.VALIDATE_PRODUCTION_STARTUP = "true";
process.env.HOSTNAME = process.env.DASHBOARD_HOSTNAME ?? "0.0.0.0";
process.env.PORT = process.env.PORT ?? "3000";
await import(server);
