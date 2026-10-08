# Stage 1: Build React app
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
# Public Cesium Ion browser token (optional). Passed as a build argument
# because .env files are kept out of the build context; without it the
# globe falls back to Cesium's bundled offline imagery.
ARG VITE_CESIUM_TOKEN=
ENV VITE_CESIUM_TOKEN=$VITE_CESIUM_TOKEN
RUN npm run build

# Stage 2: Serve with nginx
FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY deployment/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
