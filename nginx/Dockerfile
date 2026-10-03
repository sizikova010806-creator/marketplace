FROM nginx:1.27-alpine

# Копируем конфиг
COPY nginx.conf /etc/nginx/nginx.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]