# Static build of the operator console, then served by nginx proxying /api to the orchestrator.
FROM node:22-alpine AS build
WORKDIR /app
COPY apps/console/package*.json ./
RUN npm install --no-audit --no-fund
COPY apps/console/ .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY apps/console/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
