# V100 Smart Signal Bot — static SPA (no backend / DB needed)
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html vite.config.ts tsconfig*.json ./
COPY public ./public
COPY src ./src
ARG VITE_DERIV_PUBLIC_WS=wss://api.derivws.com/trading/v1/options/ws/public
ARG VITE_DERIV_SYMBOL=R_100
ARG VITE_HISTORY_COUNT=5000
ARG VITE_SOUND_ALERTS=true
ENV VITE_DERIV_PUBLIC_WS=$VITE_DERIV_PUBLIC_WS \
    VITE_DERIV_SYMBOL=$VITE_DERIV_SYMBOL \
    VITE_HISTORY_COUNT=$VITE_HISTORY_COUNT \
    VITE_SOUND_ALERTS=$VITE_SOUND_ALERTS
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
