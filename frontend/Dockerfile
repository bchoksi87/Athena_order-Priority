# ---- build stage -----------------------------------------------------------
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json .npmrc ./
RUN npm ci
COPY . .
# VITE_API_BASE_URL is baked at build time; keep the default "/api/v1" so nginx can proxy it.
ARG VITE_API_BASE_URL=/api/v1
ARG VITE_APP_ENV=prod
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL VITE_APP_ENV=$VITE_APP_ENV
RUN npm run build

# ---- runtime stage ---------------------------------------------------------
FROM nginx:1.27-alpine
# BACKEND_URL is substituted into the nginx config at container start (envsubst via the
# official image's /docker-entrypoint.d templating). Example: http://backend:8000
ENV BACKEND_URL=http://backend:8000
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1/healthz || exit 1
