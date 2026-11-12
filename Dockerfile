# iCloud Control (ex icloud-dispatcher): bot Telegram + GUI web per i container iCloud. Immagine: ghcr.io/sidimam/icloud-control.
FROM docker:cli
LABEL org.opencontainers.image.source="https://github.com/sidimam/icloud-control"
RUN apk add --no-cache bash curl jq python3 py3-flask py3-pip tzdata vips-tools vips-heif vips-poppler ffmpeg exiftool \
 && pip install --no-cache-dir --break-system-packages pyotp==2.9.0 qrcode==7.4.2 icalendar==6.1.0 recurring-ical-events==3.3.3
# Calendario (FullCalendar, MIT) e mappa (Leaflet, BSD) dentro l'immagine: la GUI non carica script da Internet.
RUN mkdir -p /opt/dispatcher/gui/static/vendor \
 && curl -fsSL -o /opt/dispatcher/gui/static/vendor/fullcalendar.js https://cdn.jsdelivr.net/npm/fullcalendar@6.1.15/index.global.min.js \
 && curl -fsSL -o /opt/dispatcher/gui/static/vendor/fullcalendar-it.js https://cdn.jsdelivr.net/npm/@fullcalendar/core@6.1.15/locales/it.global.min.js \
 && curl -fsSL -o /opt/dispatcher/gui/static/vendor/luxon.js https://cdn.jsdelivr.net/npm/luxon@3.5.0/build/global/luxon.min.js \
 && curl -fsSL -o /opt/dispatcher/gui/static/vendor/fullcalendar-luxon.js https://cdn.jsdelivr.net/npm/@fullcalendar/luxon3@6.1.15/index.global.min.js \
 && curl -fsSL -o /opt/dispatcher/gui/static/vendor/leaflet.js https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js \
 && curl -fsSL -o /opt/dispatcher/gui/static/vendor/leaflet.css https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css \
 && curl -fsSL -o /opt/dispatcher/gui/static/vendor/markercluster.js https://cdn.jsdelivr.net/npm/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js \
 && curl -fsSL -o /opt/dispatcher/gui/static/vendor/markercluster.css https://cdn.jsdelivr.net/npm/leaflet.markercluster@1.5.3/dist/MarkerCluster.css
COPY bot.sh /opt/dispatcher/bot.sh
COPY gui/ /opt/dispatcher/gui/
COPY entrypoint.sh /opt/dispatcher/entrypoint.sh
RUN chmod 755 /opt/dispatcher/bot.sh /opt/dispatcher/entrypoint.sh
ENV TZ=Europe/Rome GUI_PORT=8787 APP_DIR=/app
EXPOSE 8787
ENTRYPOINT ["/opt/dispatcher/entrypoint.sh"]
