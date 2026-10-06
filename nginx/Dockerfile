FROM nginx:alpine

COPY nginx.conf /etc/nginx/nginx.conf

# Папка для SSL сертификатов (будет смонтирована как volume)
RUN mkdir -p /etc/nginx/ssl

EXPOSE 80 443

CMD ["nginx", "-g", "daemon off;"]
