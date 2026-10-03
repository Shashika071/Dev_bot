FROM node:18-alpine

WORKDIR /app

# Copy package files first so npm install is cached as a layer
COPY package.json package-lock.json* ./
RUN npm install

# Copy source (overridden by volume mount in dev)
COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
