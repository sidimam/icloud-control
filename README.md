# iCloud Dashboard

<img src="assets/icloud-dashboard.png" width="72" alt="">

Rinnovo della sessione 2FA Apple dei container iCloud di Unraid **con un solo bot Telegram**, condiviso fra più container e con il
resto della casa. Gestisce due famiglie di container:

- **📷 foto** — [boredazfcuk/icloudpd](https://github.com/boredazfcuk/docker-icloudpd) (uno per Apple ID, libreria personale o condivisa);
- **📁 iCloud Drive** — [mandarons/icloud-drive](https://github.com/mandarons/icloud-docker) (uno per Apple ID, solo download).

> Nomi precedenti: *iCloudpd-dispatcher* (fino al 22/10/2026, solo foto) e *icloud-dispatcher* (fino al 5/11/2026). GitHub reindirizza i
> vecchi nomi; dalla 3.0 l'immagine è `ghcr.io/sidimam/icloud-dashboard`, il container `icloud-dashboard`, l'appdata
> `/mnt/user/appdata/icloud-dashboard`. Il progetto per posta, calendario, contatti, note e ricerca globale è in RepoCasa
> (`docs/progetti/icloud-completo.html`).

## Il problema

Il cookie di autenticazione Apple dura circa 30 giorni. L'immagine icloudpd sa già riautenticarsi da Telegram
(`telegram_polling=true`: legge `<user> auth` e `<user> 123456` dalla chat), ma il polling di Telegram (`getUpdates` con offset)
è esclusivo: due container sullo stesso bot si rubano i messaggi. Servirebbe un bot per container.

## La soluzione

Un piccolo container, `icloud-dashboard`, è l'unico a leggere il bot. Riconosce il prefisso `#nome` e consegna il comando
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
giorni di promemoria: aggiorna `/config/icloudpd.conf`, il template Unraid e riavvia), **Rimuovi dalla GUI**. **Nuovo utente** (dalla 2.4 solo container
📁 **iclouddr**, mandarons/icloud-drive: i container icloudpd esistenti restano gestibili ma non se ne creano più) crea cartelle, template Unraid e
container scrivendo `config.yaml`: iCloud Drive sotto `/mnt/user/documents/<Persona>/iCloud Drive` (cartelle da saltare, default `Home`),
e a scelta le foto personali (libreria `PrimarySync`, `%Y/%m`, nomi `simple`) sotto `/mnt/user/media/photos/<Persona>/Personale`
montate su `/icloud/photos/all`; controllo ogni 30 minuti (foto ogni ora), nessuna porta pubblicata: lo stato si legge qui e la variabile
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

### Riavvio automatico dei giri bloccati (dalla 2.5)

`mandarons/icloud-drive` a volte resta appeso a metà giro su una connessione morta: nessuna riga di log, rete e CPU ferme, e non
riparte da solo. Ogni 2 minuti il dispatcher guarda i container iCloud Drive accesi con un giro in corso e li **riavvia** se:

- negli ultimi 10 minuti hanno ricevuto meno di 100 KB e usato meno di 2 s di CPU (un giro sano, anche quando non scarica niente,
  scorre le cartelle e riceve centinaia di KB al minuto), oppure
- il giro dura da più di 4 ore (o di 3 volte l'ultimo giro completo, se più lungo).

Al massimo 3 riavvii in 6 ore per container: dopo, un avviso Telegram alla chat predefinita e niente più riavvii automatici per 6 ore.
Gli interventi sono in `/app/state/autoheal.json` e nella scheda dell'utente («Riavvio automatico»). Non interviene durante una
riautenticazione né sui container fermati a mano. Variabili (facoltative): `AUTOHEAL=0` per spegnerlo, `AUTOHEAL_CHECK_S` (120),
`AUTOHEAL_IDLE_MIN` (10), `AUTOHEAL_MAX_CYCLE_H` (4), `AUTOHEAL_MAX_RESTARTS` (3).

Dalla 2.4.1 la pagina principale legge i log una sola volta per container (in parallelo, cache di 15 s): prima impiegava ~7 s.

### Notifiche (dalla 2.6)

Gli avvisi automatici del dispatcher si impostano dalle variabili del template Unraid:

| Variabile | Default | Effetto |
|---|---|---|
| `TELEGRAM_CHAT_ID` | vuoto | Chat degli avvisi automatici; vuoto = `default_chat_id` di `config.json` |
| `NOTIFY_AUTOHEAL_RESTART` | `1` | Un messaggio a ogni riavvio automatico di un iclouddr bloccato |
| `NOTIFY_AUTOHEAL_GAVE_UP` | `1` | Avviso quando un container si blocca 3 volte in 6 ore e i riavvii si sospendono |
| `NOTIFY_QUIET_HOURS` | `23:00-07:30` | In questa fascia gli avvisi si accodano (`/app/state/notify_queue.json`) e arrivano insieme in un solo messaggio alla fine; vuoto = sempre subito |

`TELEGRAM_TOKEN` e `ALLOWED_CHATS` (le chat da cui il bot accetta comandi) restano come prima. Le risposte ai comandi del bot e
della GUI non passano dalle ore silenziose: arrivano sempre subito alla chat che le ha chieste.

### Ruoli (dalla 3.4)

Ogni profilo è **Amministratore** (vede tutti i container, «Nuovo utente» e «Amministrazione») oppure **Utente** (vede e gestisce
solo il proprio container, quello che si chiama `iclouddr-<profilo>`, più il proprio profilo; le altre pagine rispondono 403).
Il ruolo si sceglie dalla scheda Amministrazione; il profilo `admin` resta sempre amministratore e deve restarne almeno uno.

### Aspetto, lingua e messaggi (dalla 3.2)

- Grafica nello stile delle Impostazioni di iOS/macOS: liste raggruppate, colori di sistema Apple, icone SVG proprie nello stile dei
  simboli Apple, interruttori e controlli segmentati; ogni sezione si apre e si chiude e il browser ricorda quali sono aperte.
- **Tema** sistema/chiaro/scuro e **lingua** italiano/inglese da «Il mio profilo» (o dalla pagina di accesso): salvati nel profilo e
  in un cookie. I testi sono in italiano nel codice; le traduzioni stanno in `gui/i18n_en.py` (chiave = testo italiano).
- Ogni messaggio Telegram del bot e della GUI inizia con **`ICLOUD:`** (variabile `TELEGRAM_PREFIX`), così in chat si capisce chi
  scrive: gli altri sistemi di casa usano `HOME ASSISTANT:`, `UNRAID:`, `PROXMOX:`, `UPTIME KUMA:`, `WELLNESS:`.

### Accesso con profili e 2FA (dalla 3.0)

La GUI ha un profilo **admin** e un profilo per persona (`sdimambro`, `dmanieri`, `sofdimambro`, `mdimambro`), tutti amministratori.
Stanno in `/app/state/profiles.json` (permessi 600): hash scrypt della password, segreto TOTP, ultimo accesso.

- **Primo avvio della 3.0**: l'admin conserva la password della 2.x (`/app/state/gui_password` o `GUI_PASSWORD_HASH`); i profili
  personali nascono senza password e non entrano finché un amministratore non gliela imposta dalla scheda **Amministrazione**.
- **Il mio profilo**: cambio password e verifica in due passaggi TOTP (QR da inquadrare con l'app Password dell'iPhone, Google
  Authenticator, Bitwarden o 1Password, oppure chiave da inserire a mano; per spegnerla serve la password).
- **Amministrazione** (solo amministratori): elenco dei profili con stato di password e 2FA, impostazione o reimpostazione della
  password, spegnimento del 2FA di un profilo, nuovi profili, eliminazione (non di sé stessi né dell'admin); notifiche Telegram
  (chat, avvisi di riavvio e di blocco, ore silenziose) salvate in `/app/state/settings.json` con precedenza sulle variabili del
  template, e un messaggio di prova.
- **Recupero**: all'attivazione del 2FA la GUI mostra **8 codici di recupero** monouso (formato `k7m2-x9qp`, salvati solo come hash;
  rigenerabili dal profilo con la password). In alternativa «Password dimenticata o telefono perso?» manda un codice a 6 cifre
  (10 minuti, 5 tentativi) alla **chat Telegram privata** collegata al profilo, con cui si sceglie una password nuova e/o si spegne il
  2FA. La chat la collega la persona dal suo profilo (verifica con un codice) o un amministratore. La pagina risponde sempre allo
  stesso modo, così non rivela quali profili esistono; ogni recupero e ogni accesso con codice di recupero viene segnalato alla chat
  delle notifiche.
- **Password dell'admin dimenticata o telefono perso**: compila `GUI_RESET_PASSWORD` nel template e applica: all'avvio reimposta la
  password dell'admin e gli spegne il 2FA. Poi svuota la variabile, altrimenti lo rifà a ogni riavvio (la GUI lo segnala).
- Sessione firmata con chiave persistente (`/app/state/gui_secret`), «Ricordami» 30 giorni, cookie `HttpOnly` e `SameSite=Lax`,
  10 tentativi falliti in 10 minuti per IP (password e codice 2FA insieme), `/healthz` libero per i monitor.

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

- Il socket Docker dà a iCloud Dashboard il controllo dell'host: il container esegue solo `docker exec` sui nomi mappati, ma va trattato come root.
- I comandi sono accettati solo dalle chat in `ALLOWED_CHATS`; nessuno stato persistente oltre all'offset Telegram (`state/offset`).
- Il codice 2FA transita in chiaro nella chat Telegram: è monouso e vale pochi minuti.

Licenza MIT.
