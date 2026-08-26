# Časově omezené security výjimky

Tento registr neřídí ani neoslabuje Trivy. Blocking scan nadále kontroluje všechny HIGH a
CRITICAL nálezy bez `--ignore-unfixed`; záznamy pouze auditují upstream komponenty, pro které
2026-08-26 není publikovaná opravená Debian 13 verze.

| Advisory | Vlastník | Platnost | Expozice a mitigace |
|---|---|---|---|
| CVE-2026-14456 | Platform Security | 2026-09-30 | OpenSSL QUIC server může neomezeně alokovat paměť. Quant Lab nespouští QUIC server; OpenSSL slouží pouze klientskému HTTPS/PostgreSQL TLS. Runtime je non-root, read-only a bez capabilities. |
| CVE-2026-7210 | Platform Security | 2026-09-30 | Upstream CPython/Expat může při zpracování škodlivého XML spotřebovat CPU. Produkční API XML nepřijímá ani neparsuje a runtime nemá obecný upload endpoint. |

Vlastník při vydání opraveného upstream runtime odstraní výjimku a ověří oba produkční images
blocking Trivy scanem. Po datu platnosti je výjimka neplatná a musí být před deploymentem znovu
triagována; nesmí být automaticky prodloužena.
