# Debug schermo nero CamStack in Home Assistant

## 1. Abilita la modalità debug

Aggiungi `?debug=1` all'URL di Home Assistant, ad esempio:
```
https://homeassistant.local:8123/config/dashboard?debug=1
```
Poi apri il pannello CamStack. In alto a sinistra vedrai l'URL caricato nell'iframe.

## 2. Verifica l'URL

- **Embedded (addon)**: `http(s)://.../api/hassio_ingress/camstack` (o con trailing slash)
- **Proxy Scrypted**: `http(s)://scrypted-host/endpoint/@apocaliss92/scrypted-advanced-notifier/public/camstack`

Apri l'URL in una nuova scheda del browser. Se funziona lì ma non nell'iframe, è probabile un blocco da X-Frame-Options o CSP.

## 3. Messaggio "CamStack non caricato"

Se dopo ~12 secondi compare il messaggio rosso, l'iframe non si è caricato. Possibili cause:

| Causa | Soluzione |
|-------|-----------|
| **URL non raggiungibile** | Verifica che Scrypted/addon sia raggiungibile dalla rete in cui gira il browser (non solo da HA) |
| **localhost / IP interno** | Se usi `localhost` o un IP interno per Scrypted, il browser potrebbe non raggiungerlo. Usa l'hostname o l'IP corretto |
| **Mixed content** | HA in HTTPS e CamStack in HTTP: il browser può bloccare. Usa HTTPS per entrambi |
| **X-Frame-Options** | Scrypted o il reverse proxy potrebbero bloccare l'embedding. Controlla le intestazioni HTTP |

## 4. Schermo nero ma iframe caricato

Se l'iframe si carica (nessun messaggio rosso) ma vedi solo nero:

- **Base path errato**: CamStack deve essere compilato con il base path corretto:
  - Addon: `EXPO_PUBLIC_WEB_BASE_PATH=/api/hassio_ingress/camstack`
  - Scrypted: `EXPO_PUBLIC_WEB_BASE_PATH=/endpoint/@apocaliss92/scrypted-advanced-notifier/public/camstack`
- **Errore JavaScript**: Apri DevTools (F12) → Console e controlla errori nell'iframe (se same-origin) o nella pagina principale

## 5. Test rapido

1. Copia l'URL mostrato in debug
2. Incollalo in una nuova scheda
3. Se funziona: problema di embedding (X-Frame-Options, CSP)
4. Se non funziona: problema di URL, connettività o build di CamStack
