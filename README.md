# Vivaldi Kanban

Beheerapp voor de kanbanvoorraad, met een afzonderlijke scanfunctie voor openbare scanlinks.

## Productie-URL's

- Beheerapp: <https://wa-kanban-webapp-gearc5g7dfc2hjeu.westeurope-01.azurewebsites.net/>

  De beheerapp gebruikt Microsoft Entra-aanmelding. Gebruik een account dat toegang heeft tot de Vivaldi-tenant.

- Scanfunctie: <https://kanban-scan-function.azurewebsites.net/>

  Een scanlink heeft dit formaat: `https://kanban-scan-function.azurewebsites.net/scan/{public_token}`.
  De applicatie maakt deze links automatisch aan; vervang `{public_token}` niet handmatig.

## Azure-onderdelen

- Web App: `wa-kanban-webapp`
- Function App voor scans: `kanban-scan-function`
- SQL-database: `sqldb-kanban`
- Blob Storage-container voor artikelafbeeldingen: `artikelafbeeldingen`

De configuratie en secrets staan als Application Settings in Azure en horen niet in de repository thuis.

## Tests lokaal uitvoeren

Gebruik Python 3.11 voor de huidige runtime-dependencies en voer daarna uit:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

De Flask-tests gebruiken SQLite in geheugen en hebben geen Azure-database,
blobopslag, echte printer of live printservice nodig.
