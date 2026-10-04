# DigiClinic WhatsApp gateway (whatsapp-gateway/server.js). Only reachable on the compose network.
FROM node:20-bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium fonts-noto-color-emoji ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ENV PUPPETEER_SKIP_DOWNLOAD=true \
    CHROME_PATH=/usr/bin/chromium \
    WA_GATEWAY_HOST=0.0.0.0 \
    WA_GATEWAY_PORT=3320 \
    WA_DATA_DIR=/data/whatsapp \
    NODE_ENV=production

WORKDIR /gw
COPY whatsapp-gateway/package.json whatsapp-gateway/package-lock.json whatsapp-gateway/patch-wwebjs.js ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY whatsapp-gateway/server.js ./

RUN mkdir -p /data/whatsapp && chown -R node:node /data /gw
USER node
EXPOSE 3320
CMD ["node", "server.js"]
