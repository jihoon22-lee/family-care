FROM node:26.7.0-alpine AS builder

WORKDIR /workspace
RUN corepack enable && corepack prepare pnpm@11.22.0 --activate

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
RUN pnpm install --frozen-lockfile --filter @familycare/web...

COPY apps/web/index.html apps/web/index.html
COPY apps/web/eslint.config.js apps/web/eslint.config.js
COPY apps/web/tsconfig.app.json apps/web/tsconfig.app.json
COPY apps/web/tsconfig.json apps/web/tsconfig.json
COPY apps/web/tsconfig.node.json apps/web/tsconfig.node.json
COPY apps/web/vite.config.ts apps/web/vite.config.ts
COPY apps/web/public apps/web/public
COPY apps/web/src apps/web/src
RUN pnpm web:build

FROM nginxinc/nginx-unprivileged:1.29.8-alpine3.23 AS runtime

COPY --chown=101:101 infra/containers/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder --chown=101:101 /workspace/apps/web/dist /usr/share/nginx/html

USER 101:101
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
