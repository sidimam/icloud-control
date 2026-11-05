#!/usr/bin/env python3
"""Configurazione UNICA dei container iclouddr di casa: genera config.yaml con le stesse impostazioni per tutti.

Uso: make_config.py <container> <Persona> <apple_id> [--home] [--famiglia SharedSync-…]
Le uniche differenze ammesse sono i dati condivisi, scaricati da una sola persona per non averne più copie:
  --home      scarica anche la cartella «Home» di iCloud Drive (gli altri la saltano)
  --famiglia  scarica anche la libreria foto condivisa (id SharedSync-…) nella cartella «famiglia»
Il container monta /icloud/drive, /icloud/photos/personale/all (e /icloud/photos/famiglia/all con --famiglia).
"""
import sys

def make(container, person, apple_id, home=False, famiglia=""):
    ignore = "" if home else '''  # Home è la cartella condivisa di famiglia: la scarica una sola persona (evita copie da 22 GB)
  ignore:
    - "Home"
'''
    libs = "      - PrimarySync\n" + (f"      - {famiglia}\n" if famiglia else "")
    fam_dest = "    SharedLibrary: famiglia\n" if famiglia else ""
    return f'''# {container} — iCloud di {person} (sidimam/iclouddr: mandarons/icloud-drive + modulo PIM). Generato da make_config.py:
# configurazione identica per tutti, tranne i dati condivisi scaricati da una sola persona.
app:
  logger:
    level: "info"
    filename: "/config/icloud.log"
  credentials:
    username: "{apple_id}"
    retry_login_interval: 600
  root: "/icloud"
  region: global
  max_threads: 4
  web_ui:
    enabled: false
    host: "0.0.0.0"
    port: 8080
drive:
  destination: "drive"
  remove_obsolete: false
  sync_interval: 1800
  request_timeout: 60
{ignore}photos:
  destination: "photos"
  sync_interval: 3600
  remove_obsolete: false
  request_timeout: 60
  all_albums: false
  folder_format: "%Y/%m"
  filename_format: simple
  library_destinations:
    PrimarySync: personale
{fam_dest}  filters:
    libraries:
{libs}    file_sizes:
      - "original"
      - "live_video_original"
      - "original_alt"
'''

if __name__ == "__main__":
    a = sys.argv[1:]
    home = "--home" in a
    fam = a[a.index("--famiglia") + 1] if "--famiglia" in a else ""
    pos = [x for i, x in enumerate(a) if not x.startswith("--") and (i == 0 or a[i - 1] != "--famiglia")]
    sys.stdout.write(make(pos[0], pos[1], pos[2], home, fam))
