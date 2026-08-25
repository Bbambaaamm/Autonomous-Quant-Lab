FROM node:24.7.0-bookworm-slim AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm install --global npm@11.17.0 && npm ci --ignore-scripts
COPY frontend/ ./
ARG PUBLIC_BASE_URL=https://localhost
ENV PUBLIC_BASE_URL=$PUBLIC_BASE_URL
RUN npm run build

FROM node:24.7.0-bookworm-slim
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000 VALIDATE_PRODUCTION_STARTUP=true
RUN apt-get update \
    && apt-get upgrade --yes \
    && rm -rf /usr/local/lib/node_modules/npm \
        /usr/local/lib/node_modules/corepack \
        /usr/local/bin/corepack \
        /usr/local/bin/npm \
        /usr/local/bin/npx \
        /usr/local/bin/pnpm \
        /usr/local/bin/pnpx \
        /usr/local/bin/yarn \
        /usr/local/bin/yarnpkg \
    && groupadd --system quantlab \
    && useradd --system --gid quantlab --home /app quantlab \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build --chown=quantlab:quantlab /app/.next/standalone ./
COPY --from=build --chown=quantlab:quantlab /app/.next/static ./.next/static
COPY --from=build --chown=quantlab:quantlab /app/public ./public
USER quantlab
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/login').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"
CMD ["node","server.js"]
