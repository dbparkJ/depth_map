FROM nginx:1.27-alpine

COPY services/road_condition_web/nginx.conf /etc/nginx/conf.d/default.conf
COPY services/road_condition_web/index.html /usr/share/nginx/html/index.html
COPY services/road_condition_web/app.js /usr/share/nginx/html/app.js
COPY services/road_condition_web/viewer_core.js /usr/share/nginx/html/viewer_core.js
COPY services/road_condition_web/style.css /usr/share/nginx/html/style.css

EXPOSE 80
