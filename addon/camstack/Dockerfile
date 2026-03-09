ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache nginx

# Ingress path is /api/hassio_ingress/camstack/ - serve files under that path
# Run "npm run build:ha-addon" before building the addon to generate dist/
RUN mkdir -p /usr/share/nginx/html/api/hassio_ingress/camstack
COPY dist/. /usr/share/nginx/html/api/hassio_ingress/camstack/

COPY nginx.conf /etc/nginx/http.d/default.conf

CMD ["nginx", "-g", "daemon off;"]
