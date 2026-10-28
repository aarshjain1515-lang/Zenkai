# Build the Vite assets with the committed pnpm lockfile before building this runtime image.
FROM nginx:1.28.0-alpine
COPY frontend/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
