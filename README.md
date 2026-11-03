# icloud-dispatcher

Rinnovo della sessione 2FA Apple dei container iCloud di Unraid **con un solo bot Telegram**, condiviso fra più container e con il
resto della casa. Gestisce due famiglie di container:

- **📷 foto** — [boredazfcuk/icloudpd](https://github.com/boredazfcuk/docker-icloudpd) (uno per Apple ID, libreria personale o condivisa);
- **📁 iCloud Drive** — [mandarons/icloud-drive](https://github.com/mandarons/icloud-docker) (uno per Apple ID, solo download).

> Fino al 22 ottobre 2026 il progetto si chiamava *iCloudpd-dispatcher* e gestiva solo le foto. GitHub reindirizza il vecchio nome;
> l'immagine è ora `ghcr.io/sidimam/icloud-dispatcher`, il container `icloud-dispatcher`, l'appdata `/mnt/user/appdata/icloud-dispatcher`.

## Il problema

Il cookie di autenticazione Apple dura circa 30 giorni. L'immagine icloudpd sa già riautenticarsi da Telegram
(`telegram_polling=true`: legge `<user> auth` e `<user> 123456` dalla chat), ma il polling di Telegram (`getUpdates` con offset)
è esclusivo: due container sullo stesso bot si rubano i messaggi. Servirebbe un bot per container.

## La soluzione

Un piccolo container, `icloud-dispatcher`, è l'unico a leggere il bot. Riconosce il prefisso `#nome` e consegna il comando
al container giusto tramite il socket Docker:

| Messaggio in chat        | Effetto                                                                                  |
|--------------------------|------------------------------------------------------------------------------------------|
| `#sofia auth`            | cancella cookie+sessione di `iCloudpd-sofdimambro` e avvia `/opt/authenticate.exp` (password dal portachiavi del container); Apple invia il codice ai dispositivi dell'account |
| `#sofia 123456`          | scrive il codice in `/tmp/icloudpd/expect_input.txt` del container (expect lo attende 10 minuti) e conferma l'esito |
| `#drive-sdimambro auth`  | per un container iCloud Drive: lancia `icloud --username … --session-directory=/config/session_data` nel container (password dal keyring, salvato al primo login manuale); Apple invia il codice |
| `#drive-sdimambro 123456`| scrive il codice nella FIFO `/tmp/2fa.fifo` da cui `icloud` legge; se il login riesce (`EXIT=0` in `/tmp/2fa.log`) il container viene riavviato e riprende a scaricare |
| `#stato`                 | giorni rimanenti della sessione Apple di ogni container (foto: `DAYS_REMAINING`; Drive: scadenza reale del cookie di fiducia `X-APPLE-WEBAUTH-HSA-TRUST`, ~90 giorni, che l'app rinnova da sola finché la sessione resta viva) |
| `#sofia log`             | ultime 8 righe utili del log del container (senza gli errori innocui ripetuti a ogni giro) |
| `#sofia riavvia` / `ferma` / `avvia` | azioni rapide sul container (`docker restart/stop/start`); il riavvio fa partire subito un giro di sincronizzazione |
| `#aiuto`                 | elenco comandi                                                                           |

I container iCloudpd restano con `telegram_polling=false` e usano il bot solo per **inviare**: le loro notifiche operative (avvio, download,
«MFA re-confirmed») vanno nella chat privata dell'amministratore (`telegram_chat_id`), con titolo leggibile `notification_title=iCloud Foto di <nome>`
e `user=<nome>` così i messaggi dicono di chi si tratta. I messaggi del flusso 2FA (promemoria a 3 e 2 giorni, avvio a 1 giorno, conferme del
dispatcher) vanno invece nel **gruppo** di famiglia, dove tutti rispondono con `#nome 123456`.

Lo script `unraid/icloud_2fa_autostart.sh` (User Scripts, ogni giorno; riconosce i container dall'immagine e i nomi da `config.json`) completa il flusso: a **1 giorno** dalla scadenza avvisa
nella chat e avvia da solo la riautenticazione, così all'utente resta solo da rispondere `#nome 123456`.

## Icona dei container iCloud Drive

I container creati dalla GUI usano `assets/icloud-drive.png` (logo iCloud, 256×256 quadrato, ricavato da Wikimedia Commons), ospitata in questo repository: un'icona non quadrata viene deformata dal tab Docker di Unraid.

## Cartelle nel tab Docker di Unraid (facoltativo)

Con il plugin [FolderView3](https://github.com/chodeus/folder.view3) i container iCloud si raggruppano nel tab Docker: una cartella «iCloud Foto» con regola per nome `^iCloudpd-` e una «iCloud Drive» con `^iclouddr-`. I container creati dalla GUI del dispatcher rispettano questi prefissi (`iCloudpd-<nome>`, `iclouddr-<nome>`), quindi entrano da soli nella cartella giusta.

## Interfaccia web (porta 8787)

La GUI (Flask, nello stesso container) mostra due sezioni con intestazione — 📷 Foto iCloud (icloudpd) sopra, 📁 iCloud Drive sotto — e un riquadro per container: stato del container, giorni al rinnovo del cookie, Apple ID,
cosa scarica (libreria PrimarySync o SharedSync-…, album), cartella, chat Telegram, ultimo evento. Azioni: **Riautentica**,
**Invia codice** (alternativa a Telegram), azioni rapide **↻ Riavvia** (fa partire subito un giro), **■ Ferma**, **▶ Avvia** e **📜 Log**, **Impostazioni** (libreria, album, struttura cartelle, chat,
giorni di promemoria: aggiorna `/config/icloudpd.conf`, il template Unraid e riavvia), **Rimuovi dalla GUI**. **Nuovo utente** crea cartelle,
template Unraid e container: per le foto copiando le impostazioni da un container esistente (poi `--Initialise`), per iCloud Drive scrivendo
`config.yaml` (destinazione sotto `/mnt/user/documents`, controllo ogni 30 minuti, nessuna porta pubblicata: lo stato si legge qui) e la variabile
`ENV_CONFIG_FILE_PATH=/config/config.yaml` senza la quale l'immagine legge un config vuoto; poi il primo login manuale
`docker exec -it iclouddr-<nome> /bin/sh -c "su-exec abc icloud --username=<apple id> --session-directory=/config/session_data"` (password, codice, `y` al keyring).
Utenti e alias stanno in `/app/config.json`, condiviso con il bot; ogni utente ha `type` (`icloudpd` o `iclouddrive`), `container`, `label`, `chat_id` e, per iCloud Drive, `apple_id` e `person`. La GUI non ha autenticazione: tenerla in LAN o dietro un proxy.

### Log e blocchi (dalla 2.1)

Per i container iCloud Drive il riquadro mostra lo stato di ogni **giro** (Drive e Foto), ricavato dalle righe `Syncing drive...` /
`Drive synced` e `Syncing photos...` / `Photos synced` delle ultime 48 ore: «in corso dalle 17:19 (14 min) · l'ultimo è durato 55 min»
oppure «completato 18 min fa in 0 min», più l'ora dell'ultima riga di log. Un giro in corso da più del **doppio dell'ultimo giro completo**
(minimo un'ora), o nessuna riga di log da più di due intervalli di sincronizzazione, accende **⚠️ sembra bloccato**: di solito basta
**↻ Riavvia** (la sessione Apple resta valida, nessun codice). Caso reale del 3 nov 2026: un giro su un iCloud Drive con 8.500 cartelle
(libreria Calibre) è rimasto appeso quattro ore su una richiesta ad Apple, con il container acceso e nessun traffico.

**📜 Log** apre l'ultima parte del log del container (100–5000 righe) con errori in rosso e avvisi in arancione, ricerca, filtro «solo errori»,
aggiornamento automatico ogni 15 s e download del file. Per default nasconde gli errori innocui ripetuti a ogni giro (file che iCloud elenca
ma non lascia scaricare, come i database dei password manager, e foto senza versione alternativa), con il conteggio delle righe nascoste.

### Accesso (dalla 2.2)

La GUI chiede utente e password se il container ha `GUI_PASSWORD_HASH` (hash werkzeug/scrypt, mai la password in chiaro; `GUI_USER`,
default `admin`). Senza hash resta aperta come prima. Sessione firmata con una chiave persistente in `/app/state/gui_secret` (un riavvio non
disconnette), «Ricordami» per 30 giorni, cookie `HttpOnly` e `SameSite=Lax` (le azioni POST non partono da altri siti), 10 tentativi falliti
in 10 minuti per IP poi attesa. `/healthz` resta libero per i monitor. Per generare l'hash:

```bash
docker exec -i icloud-dispatcher python3 -c "import sys; from werkzeug.security import generate_password_hash as g; print(g(sys.stdin.read().strip()))"
```

(scrivi la password, Invio, Ctrl-D) e incolla il risultato nella variabile del template.

## Installazione (Unraid)

1. Copia `bot.sh` in `/mnt/user/appdata/icloudpd-dispatcher/bot.sh`.
2. Importa `unraid/my-iCloudpd-dispatcher.xml` in `/boot/config/plugins/dockerMan/templates-user/` e compila
   `TELEGRAM_TOKEN` e `ALLOWED_CHATS` (chat id separate da virgola: solo da queste chat vengono accettati i comandi).
3. Immagine pronta: `ghcr.io/sidimam/icloudpd-dispatcher:latest` (amd64/arm64, costruita da GitHub Actions a ogni push su main). Avvia il container con i mount del template:
   socket Docker, `/app` (config e stato), `/templates` (template Unraid), `/appdata`, `/photos`; porta 8787 per la GUI.
4. Adatta la mappa nome → container in `bot.sh` (`declare -A C=(...)`) e in `unraid/icloudpd_2fa_autostart.sh`.
5. Nei container iCloudpd: `notification_type=telegram`, `telegram_token`, `telegram_chat_id`, `telegram_polling=false`,
   `notification_days=3`. Attenzione: se il container era già inizializzato, i valori vanno cambiati anche in `/config/icloudpd.conf`,
   perché l'immagine non riscrive quelli esistenti.

## Sicurezza

- Il socket Docker dà al dispatcher il controllo dell'host: il container esegue solo `docker exec` sui nomi mappati, ma va trattato come root.
- I comandi sono accettati solo dalle chat in `ALLOWED_CHATS`; nessuno stato persistente oltre all'offset Telegram (`state/offset`).
- Il codice 2FA transita in chiaro nella chat Telegram: è monouso e vale pochi minuti.

Licenza MIT.
