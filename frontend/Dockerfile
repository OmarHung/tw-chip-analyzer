# tw_chip_analyzer 前端（Next.js 16）。
# NEXT_PUBLIC_API_BASE 於 build 時烤進 client bundle,故為 build ARG。
FROM node:20-slim AS build
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@10.33.0 --activate
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
ARG NEXT_PUBLIC_API_BASE
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
RUN pnpm build

# --- runtime ---
FROM node:20-slim
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@10.33.0 --activate
ENV NODE_ENV=production
# 只帶執行期需要的產物
COPY --from=build /app/.next ./.next
COPY --from=build /app/public ./public
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/package.json ./package.json
COPY --from=build /app/next.config.ts ./next.config.ts
EXPOSE 3000
CMD ["pnpm", "start", "-p", "3000"]
