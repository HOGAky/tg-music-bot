FROM nginx:alpine

# curl нужен healthcheck'у из docker-compose.yml (ТЗ v3.0, п. 3.3):
#   curl -f http://localhost/nginx-health
RUN apk add --no-cache curl

COPY nginx.conf /etc/nginx/nginx.conf

# Папка для SSL сертификатов (будет смонтирована как volume)
RUN mkdir -p /etc/nginx/ssl

EXPOSE 80 443

CMD ["nginx", "-g", "daemon off;"]
