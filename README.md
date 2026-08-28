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
