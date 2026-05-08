FROM node:22-bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-dejavu-core fontconfig \
 && rm -rf /var/lib/apt/lists/* \
 && fc-cache -f

WORKDIR /app

ENV PNPM_HOME=/usr/local/pnpm
ENV PATH=$PNPM_HOME:$PATH
ENV NODE_ENV=production

RUN corepack enable && corepack prepare pnpm@10.0.0 --activate

COPY package.json pnpm-lock.yaml ./

RUN pnpm install --frozen-lockfile

COPY src ./src
COPY tsconfig.json ./

CMD ["pnpm", "run", "start"]
