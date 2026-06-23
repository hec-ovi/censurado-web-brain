# The presentation layer is buildless: copy the static ES-module app and the
# nginx config straight into the image. No bundler, no node in the runtime.
# The dev test runner (node --test) never ships here.
FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY src/ /usr/share/nginx/html/
