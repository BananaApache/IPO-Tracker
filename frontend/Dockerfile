FROM node:20-slim

# Install build essentials for binary packages
RUN apt-get update && apt-get install -y \
    python3 \
    make \
    g++ \
    libc6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package*.json ./
# Clean install to ensure no Mac binaries are present
RUN npm ci

COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev"]