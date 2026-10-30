# Local web UI (see compose.yaml): the same vite build as Vercel, served by nginx.
# VITE_* are baked in at build time; the browser calls the worker directly.
FROM node:24-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_URL
ARG VITE_MEDIA_URL
ARG VITE_API_TOKEN
RUN npx vite build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
