# iCloud Dashboard (ex icloud-dispatcher): bot Telegram + GUI web per i container iCloud. Immagine: ghcr.io/sidimam/icloud-dashboard.
FROM docker:cli
LABEL org.opencontainers.image.source="https://github.com/sidimam/icloud-dashboard"
RUN apk add --no-cache bash curl jq python3 py3-flask py3-pip tzdata \
 && pip install --no-cache-dir --break-system-packages pyotp==2.9.0 qrcode==7.4.2
COPY bot.sh /opt/dispatcher/bot.sh
COPY gui/ /opt/dispatcher/gui/
COPY entrypoint.sh /opt/dispatcher/entrypoint.sh
RUN chmod 755 /opt/dispatcher/bot.sh /opt/dispatcher/entrypoint.sh
ENV TZ=Europe/Rome GUI_PORT=8787 APP_DIR=/app
EXPOSE 8787
ENTRYPOINT ["/opt/dispatcher/entrypoint.sh"]
