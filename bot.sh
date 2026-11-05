#!/bin/bash
# iCloud Dashboard (ex icloud-dispatcher): un solo bot Telegram di casa per il rinnovo 2FA Apple dei container iCloud:
#   • boredazfcuk/icloudpd  (foto)          → tipo "icloudpd"   : expect /opt/authenticate.exp + /tmp/icloudpd/expect_input.txt
#   • mandarons/icloud-drive (iCloud Drive) → tipo "iclouddrive": `icloud --username … --session-directory` con il codice via FIFO
# Comandi accettati SOLO dalle chat in ALLOWED_CHATS (chat privata di Simone e gruppo di famiglia):
#   #sofia auth        → avvia la riautenticazione del container di "sofia" (Apple manda il codice ai dispositivi dell'account)
#   #sofia 123456      → consegna il codice al container
#   #stato             → giorni rimanenti del cookie di fiducia Apple di ogni container
#   #sofia log         → ultime righe utili del log del container (senza gli errori innocui ripetuti)
#   #sofia riavvia | ferma | avvia → azioni rapide sul container (il riavvio fa partire subito un giro)
#   #aiuto             → elenco comandi
# Utenti, container, tipo e alias stanno in config.json (condiviso con la GUI). Richiede TELEGRAM_TOKEN e ALLOWED_CHATS.
set -u
API="https://api.telegram.org/bot${TELEGRAM_TOKEN}"
STATE=/app/state; mkdir -p "$STATE"; OFF="$STATE/offset"; [ -f "$OFF" ] || echo 0 > "$OFF"
CFG="${APP_DIR:-/app}/config.json"
declare -A C T A U   # C[nome]=container  T[nome]=tipo  A[nome]=apple id  U[alias]=nome
load_cfg(){ C=(); T=(); A=(); U=()
  while IFS=$'\t' read -r n c t a; do [ -n "$n" ] && { C[$n]=$c; T[$n]=${t:-icloudpd}; A[$n]=$a; U[$n]=$n; }; done < <(jq -r '.users|to_entries[]|[.key,.value.container,(.value.type//"icloudpd"),(.value.apple_id//"")]|@tsv' "$CFG" 2>/dev/null)
  while IFS=$'\t' read -r a n; do [ -n "$a" ] && [ -n "${C[$n]:-}" ] && U[$a]=$n; done < <(jq -r '.aliases|to_entries[]|[.key,.value]|@tsv' "$CFG" 2>/dev/null)
  NAMES=$(jq -r '.users|keys|join(" ")' "$CFG" 2>/dev/null)
  }
load_cfg
log(){ echo "$(date '+%F %T') $*"; }
NOISE="ObjectNotFoundException|Could not find document|File not found in iCloud Drive|not found on server\. Skipping"   # errori innocui ripetuti a ogni giro (come NOISE nella GUI)
say(){ curl -s -X POST "$API/sendMessage" -d chat_id="$1" --data-urlencode text="${TELEGRAM_PREFIX:-ICLOUD:} $2" >/dev/null; }   # ogni risposta inizia con il mittente
allowed(){ case ",${ALLOWED_CHATS}," in *",$1,"*) return 0;; *) return 1;; esac; }
icon(){ [ "${T[$1]}" = iclouddrive ] && echo "📁" || echo "📷"; }
# ---- icloudpd (foto)
p_cookie(){ docker exec "$1" sh -c 'ls /config | grep -E "^[a-z0-9]+(com|it)$" | head -1'; }
p_running(){ docker exec "$1" sh -c 'pgrep -f authenticate.exp >/dev/null' 2>/dev/null; }
p_start(){ local ck; ck=$(p_cookie "$1")
  docker exec "$1" sh -c "rm -f /config/${ck} /config/${ck}.session; mkdir -p /tmp/icloudpd; : > /tmp/icloudpd/expect_input.txt; chmod 666 /tmp/icloudpd/expect_input.txt; nohup /usr/bin/expect /opt/authenticate.exp >/tmp/icloudpd/reauth.log 2>&1 &"; }
p_code(){ docker exec "$1" sh -c "printf '%s\n' '$2' >> /tmp/icloudpd/expect_input.txt"; }
p_days(){ docker exec "$1" cat /config/DAYS_REMAINING 2>/dev/null | tr -dc '0-9-'; }
p_ok(){ local ck; ck=$(p_cookie "$1"); [ -n "$ck" ] && docker exec "$1" grep -q "X-APPLE-WEBAUTH-USER" "/config/$ck" 2>/dev/null; }
# ---- iclouddrive (mandarons): la password è nel keyring del container (primo login manuale con "y" a "Save password in keyring?")
d_running(){ docker exec "$1" sh -c 'pgrep -f "icloud --username" >/dev/null' 2>/dev/null; }
d_start(){ # $1 container $2 apple id — il codice arriva da una FIFO tenuta aperta in lettura/scrittura (fd 3) così icloud non si blocca
  docker exec "$1" sh -c "rm -f /tmp/2fa.fifo /tmp/2fa.log; mkfifo -m 666 /tmp/2fa.fifo; nohup sh -c 'exec 3<>/tmp/2fa.fifo; su-exec abc icloud --username=$2 --session-directory=/config/session_data <&3 >/tmp/2fa.log 2>&1; echo EXIT=\$? >>/tmp/2fa.log; rm -f /tmp/2fa.fifo' >/dev/null 2>&1 &"; }
d_code(){ docker exec "$1" sh -c "printf '%s\n' '$2' > /tmp/2fa.fifo"; }
d_days(){ # giorni alla scadenza del cookie di fiducia Apple (X-APPLE-WEBAUTH-HSA-TRUST, ~90 gg; l'app lo rinnova da sola finche' la sessione vive)
  docker exec "$1" sh -c 'f=$(ls /config/session_data/* 2>/dev/null | grep -v "\.session$" | head -1); [ -n "$f" ] || exit 0; e=$(sed -n "s/^Set-Cookie3: X-APPLE-WEBAUTH-HSA-TRUST=.*expires=\"\([^\"]*\)Z\".*/\1/p" "$f" | head -1); [ -n "$e" ] || exit 0; echo $(( ($(date -u -d "$e" +%s) - $(date +%s)) / 86400 ))' 2>/dev/null; }
d_ok(){ docker exec "$1" sh -c 'grep -q "EXIT=0" /tmp/2fa.log 2>/dev/null && ! grep -qi "failed\|invalid\|error" /tmp/2fa.log'; }
# ---- dispatch per tipo
running(){ [ "${T[$1]}" = iclouddrive ] && d_running "${C[$1]}" || { [ "${T[$1]}" != iclouddrive ] && p_running "${C[$1]}"; }; }
start_auth(){ if [ "${T[$1]}" = iclouddrive ]; then d_start "${C[$1]}" "${A[$1]}"; else p_start "${C[$1]}"; fi; }
send_code(){ if [ "${T[$1]}" = iclouddrive ]; then d_code "${C[$1]}" "$2"; else p_code "${C[$1]}" "$2"; fi; }
days(){ if [ "${T[$1]}" = iclouddrive ]; then d_days "${C[$1]}"; else p_days "${C[$1]}"; fi; }
ok(){ if [ "${T[$1]}" = iclouddrive ]; then d_ok "${C[$1]}"; else p_ok "${C[$1]}"; fi; }
log "avvio; chat ammesse: ${ALLOWED_CHATS}; utenti: ${NAMES}"
while true; do
  OFFSET=$(( $(cat "$OFF") + 1 ))
  R=$(curl -s --max-time 70 "$API/getUpdates" -d offset="$OFFSET" -d timeout=50 -d allowed_updates='["message"]') || { sleep 5; continue; }
  echo "$R" | jq -e '.ok' >/dev/null 2>&1 || { log "risposta non valida: ${R:0:120}"; sleep 10; continue; }
  while IFS=$'\t' read -r UPD CHAT TEXT FROM; do
    [ -z "$UPD" ] && continue
    load_cfg
    echo "$UPD" > "$OFF"
    allowed "$CHAT" || { log "ignoro chat $CHAT ($FROM): $TEXT"; continue; }
    TXT=$(echo "$TEXT" | tr 'A-Z' 'a-z' | sed -E 's/^\s+|\s+$//g')
    case "$TXT" in
      "#aiuto"|"#help") say "$CHAT" "Comandi iCloud: #stato · #<nome> auth (riautentica) · #<nome> 123456 (codice Apple) · #<nome> log · #<nome> riavvia|ferma|avvia. Nomi: $NAMES (📷 foto, 📁 iCloud Drive; valgono anche gli alias dei container).";;
      "#stato") M="iCloud, giorni al rinnovo della sessione Apple:"; for n in $NAMES; do d=$(days "$n"); M="$M"$'\n'"$(icon "$n") $n: ${d:-n/d}"; running "$n" && M="$M (autenticazione in corso)"; done; say "$CHAT" "$M";;
      \#*)
        N=${TXT#\#}; N=${N%% *}; ARG=${TXT#* }; N=${U[$N]:-}
        [ -z "$N" ] && { say "$CHAT" "Nome '${TXT#\#}' sconosciuto. Nomi: $NAMES"; continue; }
        CT=${C[$N]}; I=$(icon "$N")
        if [ "$ARG" = "auth" ]; then
          if running "$N"; then say "$CHAT" "⏳ $I $N: autenticazione già in corso, aspetto il codice (#$N 123456)."; continue; fi
          [ "${T[$N]}" = iclouddrive ] && [ -z "${A[$N]}" ] && { say "$CHAT" "⚠️ $N: manca l'Apple ID in config.json (campo apple_id)."; continue; }
          start_auth "$N"; log "$FROM avvia auth su $CT"
          say "$CHAT" "🔐 $I $N: riautenticazione avviata su $CT. Apple sta inviando il codice a 6 cifre ai dispositivi dell'account. Rispondi entro 10 minuti con: #$N 123456"
        elif [[ "$ARG" =~ ^[0-9]{6}$ ]]; then
          if ! running "$N"; then say "$CHAT" "⚠️ $I $N: nessuna autenticazione in corso. Prima: #$N auth"; continue; fi
          send_code "$N" "$ARG"; log "$FROM consegna codice a $CT"
          sleep 20
          if running "$N"; then say "$CHAT" "⏳ $I $N: codice consegnato, Apple sta verificando…"; sleep 25; fi
          if ok "$N"; then
            [ "${T[$N]}" = iclouddrive ] && docker restart "$CT" >/dev/null 2>&1
            d=$(days "$N"); say "$CHAT" "✅ $I $N: autenticazione riuscita, sessione rinnovata${d:+ (~$d giorni)}. La sincronizzazione riprende da sola."
          else say "$CHAT" "❌ $I $N: autenticazione non riuscita (codice errato o scaduto). Riprova con #$N auth"; fi
        elif [ "$ARG" = "log" ]; then
          L=$(docker logs --tail 400 "$CT" 2>&1 | sed -E 's/\x1b\[[0-9;]*m//g' | grep -vE "$NOISE" | tail -8 | sed -E 's/^([0-9-]+ [0-9:]+),[0-9]+ :: ([A-Z]+) :: \S+ :: \S+ :: [0-9]+ :: /\1 \2 /' | cut -c1-200)
          say "$CHAT" "📜 $I $N ($CT), ultime righe:"$'\n'"${L:-nessuna riga}"
        elif [[ "$ARG" =~ ^(riavvia|ferma|avvia)$ ]]; then
          case "$ARG" in riavvia) A2=restart; F="riavviato: il giro di sincronizzazione riparte subito";; ferma) A2=stop; F="fermato";; avvia) A2=start; F="avviato";; esac
          if docker "$A2" "$CT" >/dev/null 2>&1; then say "$CHAT" "✅ $I $N: $CT $F."; log "$FROM: docker $A2 $CT"; else say "$CHAT" "❌ $I $N: docker $A2 $CT non riuscito."; fi
        else say "$CHAT" "Comando non capito. Usa: #$N auth · #$N 123456 · #$N log · #$N riavvia|ferma|avvia"; fi;;
      *) ;;
    esac
  done < <(echo "$R" | jq -r '.result[] | [.update_id, .message.chat.id, (.message.text // ""), (.message.from.first_name // "")] | @tsv')
done
