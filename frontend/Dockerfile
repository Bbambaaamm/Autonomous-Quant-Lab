FROM node:24.7.0-bookworm-slim AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm install --global npm@11.17.0 && npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

FROM node:24.7.0-bookworm-slim
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000
RUN groupadd --system quantlab && useradd --system --gid quantlab --home /app quantlab
WORKDIR /app
COPY --from=build --chown=quantlab:quantlab /app/.next/standalone ./
COPY --from=build --chown=quantlab:quantlab /app/.next/static ./.next/static
COPY --from=build --chown=quantlab:quantlab /app/public ./public
USER quantlab
EXPOSE 3000
CMD ["node","server.js"]
