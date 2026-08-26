FROM node:24.18.1-trixie-slim AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm install --global npm@11.17.0 && npm ci --ignore-scripts
COPY frontend/ ./
ARG PUBLIC_BASE_URL=https://localhost
ENV PUBLIC_BASE_URL=$PUBLIC_BASE_URL
RUN npm run build

FROM gcr.io/distroless/nodejs24-debian13:nonroot
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000 VALIDATE_PRODUCTION_STARTUP=true
WORKDIR /app
COPY --from=build --chown=65532:65532 /app/.next/standalone ./
COPY --from=build --chown=65532:65532 /app/.next/static ./.next/static
COPY --from=build --chown=65532:65532 /app/public ./public
USER 65532:65532
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["/nodejs/bin/node", "-e", "fetch('http://127.0.0.1:3000/login').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"]
CMD ["server.js"]
