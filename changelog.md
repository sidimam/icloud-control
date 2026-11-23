# Registro delle modifiche — iCloud Control

Una voce per ogni modifica, la più recente in alto, raggruppate per giorno. Serve a ricostruire lo stato del progetto
senza rileggere le conversazioni: ogni commit aggiunge qui la sua voce (cosa, perché, dove, come è stato provato).
Le voci fino al 16/11/2026 sono ricostruite dalla storia git (messaggio del commit e hash).

## 2026-11-23
- 3.26.0: **selettore dell'account nei Contatti** (richiesta: «non si distingue quali sono presenti nell'account icloud e quali in google»). Accanto ai filtri un menu con «Tutti gli account», per ogni persona «tutti gli account» (se ne ha più di uno) e ogni account con il numero di schede, raggruppati per persona; cambia la pagina appena si sceglie. «Presente in» ora dice persona e account (es. «Simone · Gmail») e le schede collegate hanno l'account nel titolo. L'unione delle persone (stessa e-mail o telefono) avviene dopo il filtro, quindi dentro un account conta solo quell'account. Provato col client di test di Flask nel container (copia di `gui` con i file nuovi, utente admin in sessione): tutti 4.218 persone da 4.990 schede; Simone · Gmail 3.614 da 3.672; Simone · iCloud 91 da 91; Simone tutti gli account 3.651 da 3.763; vista Dettagli e lingua ok. File: `gui/app.py`, `gui/templates/contacts.html`, `gui/i18n_en.py`, `README.md`.
- 3.25.7: le persone nuove nascono con iclouddr 0.2.12, che copia i contatti degli account Gmail (CardDAV di Google con la password per app). Con la 0.2.11 un account Gmail con solo «Contatti» restava «in attesa» per sempre: è successo a «Gmail - Simone», aggiunto da «Account collegati» il 23/11. I quattro container esistenti sono già passati alla 0.2.12 (template Unraid aggiornati, backup `.bak-20261123`). File: `gui/app.py` (`ICLOUDDR_IMAGE` predefinito), `README.md`.

## 2026-11-16
- Nuovo `changelog.md`: storia ricostruita dai commit (strumento `tools/changelog_da_git.sh` della cartella Claude); da qui in poi ogni commit aggiunge la sua voce in cima.

## 2026-11-12
- 3.25.6: Pulizia, la preparazione gira in background (il pannello non resta fermo su «Analisi in corso») e l'elenco di iCloud si risolve per radice invece che file per file (Sofia da 145 a 18 s) (`1965eef`)
- 3.25.5: nuove persone con iclouddr 0.2.11 (avvio senza chown ricorsivo dei mount) (`956200f`)
- 3.25.4: contatti con la loro frequenza (PIM_CONTACTS_INTERVAL, 24 ore), note e promemoria ogni 24 ore; nuove persone con iclouddr 0.2.10 (`fda3bbe`)
- 3.25.3: Posta, la Pulizia propone solo le copie doppie che esistono solo sul NAS (stato di mbsync), i doppi anche su iCloud diventano un elenco; Calendario, doppioni solo nello stesso calendario e nomi dei calendari al posto degli id (`ea6ce61`)
- 3.25.2: Posta, doppioni solo nella stessa cartella (lo stesso messaggio in due cartelle non è un doppione); spiegazioni: un file eliminato che fosse ancora su iCloud viene riscaricato (`27caf46`)
- 3.25.1: icona del Cestino nella barra delle schede, il cestino si apre da qualunque scheda (`828eb9e`)
- 3.25.0: Pulizia anche in Foto e Posta (doppioni, cancellati da iCloud o dal server) e doppioni su iCloud per Note, Promemoria, Calendario e Contatti; Cestino con elenco, ripristino nel posto originale e svuotamento; nomi dei gruppi più chiari (`515eeb7`)
- 3.24.1: Pulizia, un file non più su iCloud è un doppione solo se la copia identica resta (su iCloud o fuori dalla copia), mai un altro file non più su iCloud (`315f63d`)
- 3.24.0: colonna delle persone in ogni scheda, Utenti dentro il Riepilogo con vista per persona, gestione in Profilo e amministrazione, Pulizia in Files (doppioni e file solo sul NAS) (`5bdcb80`)
- 3.23.0: account collegati in «Il mio profilo» (esempi col nome della persona, spiegazioni dietro la i); profilo e amministrazione in un'unica scheda (`43f5b8d`)
- README: attribuzione dell'icona (Andrean Prabowo, Flaticon) (`28f85a6`)
- 3.22.0: iCloud Dashboard diventa iCloud Control (repo, immagine ghcr.io/sidimam/icloud-control, container e appdata icloud-control); nuova icona su sfondo bianco (`5bb30ea`)

## 2026-11-09
- README: accesso da fuori su icloud.manieridimambro.it dietro Cloudflare Access (`214f800`)
- 3.21.1: «copia in più sul NAS» per le varianti nome__… rimaste da un vecchio schema di nomi quando su iCloud la stessa foto c'è con un altro nome (23 delle 40 di Daniela) (`a685690`)
- 3.21: icona su iCloud / solo sul NAS in ogni scheda (dall'elenco di iclouddr 0.2.9 per file e foto, dall'account per posta, calendari e contatti); nuovi utenti con iclouddr 0.2.9 (`98ed455`)
- 3.20.1: file manager, le operazioni in background non leggono più la sessione (annulla non veniva registrato con i profili attivi); avviso nella copia di iCloud ricavato dalla cartella di Drive (`559bd6c`)
- 3.20: file manager nella scheda Files (uno o due pannelli, selezione multipla, copia/taglia/incolla, sposta, rinomina, nuova cartella, cestino con annulla/ripeti, anteprima, ordinamenti) (`71e9ece`)

## 2026-11-08
- README: autoheal 3.19.4 e chat degli avvisi (`74c2ce9`)
- 3.19.4: avvisi Telegram leggibili (niente \u e \n letterali); autoheal senza la regola «lento e quasi fermo» che riavviava giri sani, limite di 4 h solo se il giro non scarica (`1eff9de`)

## 2026-11-07
- 3.19.3: foto non iCloud in media/photos/<persona>/Archivio indicizzate come personali (`1d31ba7`)

## 2026-11-06
- 3.19.2: nuovi utenti con iclouddr 0.2.8 (`0ef54e7`)
- 3.19.1: riavvio automatico anche per i giri lunghi il doppio del solito con il programma quasi fermo (blocco di Sofia del 6/11) (`0ad2acb`)
- 3.19: chiavi Immich (asset.read) per libreria aggiunte con + dalla scheda dell'utente; ricerca mista API/lettura diretta che copre sempre tutte le foto (`d313721`)
- 3.18: scheda Files, selettore di vista in ogni scheda, nuovo ordine delle schede, ora e meteo nel Riepilogo, nome vero prima del numero nei contatti uniti (`4a5bb82`)
- 3.17: descrizioni dietro la «i» con popup; ricerca foto su tutta la famiglia (chiavi Immich dedicate asset.read, una per utente, oppure lettura diretta dell'analisi di Immich) (`b5d9b95`)
- 3.16.1: foto in media/photos/<persona>/iCloud/<libreria> (default di Nuovo utente e documentazione) (`2a1ade1`)
- 3.16: Utenti in stile pannello iCloud (Account › servizi › percorsi, barra dello spazio), disposizione orizzontale/verticale, niente nome del container nei titoli (`7e4d3c0`)
- 3.15: ricerca di cosa c'è nelle foto tramite Immich (chiave API in Amministrazione); ⓘ sulle miniature; pannello informazioni chiudibile restando sulla foto (`922a6e4`)
- 3.14: fusi orari (TZID degli eventi, scelta del fuso), data di oggi nel Calendario, festività italiane di Apple, campo Cerca in Note/Promemoria/Posta/Calendario, nomi dei contatti dal campo N, numeri senza nome e numeri ripiegati (`c7a0b36`)
- 3.13: calendari condivisi riconosciuti e mostrati una volta sola; voce Famiglia in Calendario e Promemoria come in Foto (`2b59651`)
- 3.12.1: menu Promemoria → Utenti → lente di Cerca (solo icona); nuovi utenti con iclouddr 0.2.7 (`5a2b9af`)
- 3.12: pagina Riepilogo con anelli, spazio su disco vero e dettagli per persona; log con ricerca e filtri in Amministrazione; filtri (nome, estensione, date, ordinamento, ordine) in tutte le schede; indice della posta più veloce e salvato a blocchi (`aabfdf8`)
- 3.11.1: l'indice legge posta, calendari e contatti dalla cartella vera dell'account (iCloud/…) e toglie quelli del percorso vecchio; cartelle e liste filtrate anche per persona (Note, Promemoria, Posta) (`b122fb3`)

## 2026-11-05
- 3.11: note, promemoria e file di iCloud Drive condivisi in blu (da iclouddr 0.2.6); scheda Foto con filtro Tutti, Famiglia e persone (`8177a87`)
- 3.10: riavvio automatico basato sulla CPU del solo programma iclouddr; Nuovo utente con la configurazione unica (iclouddr_config.py) e iclouddr 0.2.5 (`44ecc34`)
- 3.9.3: account «in corso» con i messaggi già scaricati; il riavvio automatico ignora i contatori azzerati dopo un riavvio (`d7a20fd`)
- 3.9.2: il visualizzatore mostra subito la miniatura e poi la versione nitida; precarica la foto prima e dopo (`ad1aa7b`)
- 3.9.1: il visualizzatore delle foto si apre sopra la mappa (strati di Leaflet isolati) (`639a7f5`)
- 3.9: foto nell'indice, galleria e visualizzatore, mappa con raggruppamenti e drill-down, miniature (vips/ffmpeg) e metadati (exiftool) per ogni file (`ca2cd9f`)
- 3.8.4: Nuovo utente con iCloud Drive in documents/<Persona>/iCloud/iCloud Drive (`484bb94`)
- 3.8.3: cartella dell'account = nome del servizio (iCloud, Gmail, Outlook), etichetta solo per i doppioni (`b4314bb`)
- 3.8.2: Nuovo utente con iclouddr 0.2.2 (`8848bd9`)
- 3.8.1: Nuovo utente con iclouddr 0.2.1 (`bb2c7ab`)
- 3.8: Nuovo utente crea il container con ghcr.io/sidimam/iclouddr (PIM, cartella iCloud e della persona) (`68c02e7`)
- 3.7: schede Calendario (viste, colori, ricorrenze, mappa), Posta, Contatti uniti; anteprima dei file; Esci nel profilo (`d8032cc`)
- 3.6: indice iclouddb (SQLite FTS5) e schede Cerca, Note, Promemoria (`c99d0e9`)
- 3.5: account collegati (iCloud, Gmail, Outlook, IMAP) nella scheda utente, Sincronizza ora, stato di posta/calendari/contatti (`fd84498`)
- 3.4: ruoli Amministratore/Utente (l'utente vede solo il proprio container) (`1a9d677`)
- 3.3: sezione «Cosa scarica» nella scheda utente (Drive, Foto, Note, Promemoria, Posta, Calendario, Contatti con cartelle, frequenza e ultimo giro) (`036d088`)
- 3.2.1: stato del modulo PIM (note e promemoria) nella scheda utente (`8b94c4f`)
- 3.2: grafica in stile Apple con sezioni ripiegabili, tema chiaro/scuro/sistema, italiano/inglese, prefisso ICLOUD: nei messaggi Telegram (`112973c`)
- 3.1: recupero dell'accesso con codici monouso e codice Telegram nella chat privata del profilo (`d6d6941`)
- 3.0: iCloud Dashboard (ex icloud-dispatcher): profili con password e 2FA TOTP, scheda Amministrazione con notifiche, nuova icona (`7a85e76`)

## 2026-11-04
- Notifiche Telegram configurabili dal template: chat, avvisi di riavvio e di blocco, ore silenziose con riepilogo (`fff86fc`)

## 2026-11-03
- Riavvio automatico dei container iCloud Drive bloccati a metà giro (rete e CPU ferme o giro troppo lungo), max 3 in 6 ore (`e5ec7c4`)
- GUI più veloce: una sola lettura dei log per container, in parallelo e in cache 15 s (`f8ed7e5`)
- Nuovo utente: solo container iclouddr (iCloud Drive + foto personali facoltative), niente più creazione di container icloudpd (`ba5b626`)
- GUI: pagina Password e variabile GUI_RESET_PASSWORD per reimpostare la password dimenticata (`32676fc`)
- GUI: accesso con utente e password (GUI_USER, GUI_PASSWORD_HASH), sessione persistente, limite ai tentativi (`b8676bd`)
- GUI e bot: log per utente, stato dei giri di sincronizzazione con avviso di blocco, azioni rapide avvia/ferma/riavvia (`f519e15`)

## 2026-10-23
- iCloud Drive: giorni alla scadenza dal cookie di fiducia X-APPLE-WEBAUTH-HSA-TRUST (~90 gg) invece di 30 meno l'eta' del file di sessione (che l'app riscrive di continuo) (`80cf838`)
- Icona quadrata per i container iCloud Drive (assets/icloud-drive.png) al posto di quella non quadrata (`cc42b0c`)
- README: cartelle Docker con FolderView3 (`a3f124e`)

## 2026-10-22
- Nuovi container iCloud Drive senza web UI né porta pubblicata: lo stato si legge nella GUI del dispatcher (`0572509`)
- GUI: due sezioni per tipo (Foto sopra, iCloud Drive sotto) con intestazione; etichetta Drive = nome della persona (`b573c4f`)
- GUI: la scheda foto dichiara il tipo (KeyError 'type' nel raggruppamento per persona) (`b0c2142`)
- icloud-dispatcher 2.0: rinominato da iCloudpd-dispatcher; supporto ai container iCloud Drive (mandarons/icloud-drive): tipo per utente in config.json, riautenticazione via icloud CLI con codice da FIFO, giorni dalla sessione, GUI con schede per persona (Drive sotto Foto), nuovo utente Drive con config.yaml e template, autostart per entrambi i tipi (`f38a9f2`)
- Autostart: promemoria 3/2 giorni e avvio nel gruppo; notifiche operative dei container nella chat privata; README (`e937084`)
- Template e README: immagine da ghcr.io (`e862e86`)
- GitHub Actions: immagine su ghcr.io/sidimam/icloudpd-dispatcher (amd64+arm64) (`46cb9e3`)
- GUI web (Flask) su 8787: utenti, Telegram, cosa scaricare, rinnovo 2FA; config.json condiviso col bot; immagine propria (`f1cd964`)
- Alias con i nomi dei container (#sdimambro ecc.) (`3c33c6e`)
- bot.sh: UID è riservata in bash, rinominata UPD (`96435db`)
- Icona del container (`ed7b75c`)
- iCloudpd-dispatcher: bot Telegram unico per il rinnovo 2FA dei container icloudpd (`60b0208`)
