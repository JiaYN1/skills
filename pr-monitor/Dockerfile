FROM node:24-bookworm-slim AS deps

WORKDIR /app
COPY package*.json ./
RUN npm ci

FROM deps AS build

WORKDIR /app
COPY . .
RUN npm run build
RUN npm prune --omit=dev

FROM node:24-bookworm-slim AS runner

ENV NODE_ENV=production \
    TZ=Asia/Shanghai \
    DATA_DIR=/app/data \
    PORT=3000

WORKDIR /app
RUN mkdir -p /app/data

COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/server ./server
COPY --from=build /app/dist ./dist
COPY --from=build /app/config ./config
COPY --from=build /app/package.json ./package.json

EXPOSE 3000
VOLUME ["/app/data"]

CMD ["node", "server/index.js"]
