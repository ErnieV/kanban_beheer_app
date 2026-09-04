# Artikelstandaard en Aanvulhoeveelheid in plaats van Max

---
status: accepted
---

De Kanban-regel gebruikt voortaan een Artikel-Kanbanstandaard met `Min` en `Aanvulhoeveelheid`; het absolute begrip `Max` wordt volledig verwijderd uit domein, interface, printdata en Badgy-contract. Voor een voorraadpositie geldt de artikelstandaard tenzij de effectieve Min of Aanvulhoeveelheid werkelijk afwijkt. Een wijziging van de standaard werkt automatisch door op posities zonder afwijking en toont vooraf alleen een overzicht van het aantal betrokken posities.

## Migratie

Bestaande waarden worden eenmalig omgerekend met `Aanvulhoeveelheid = Max - Min`. Per Artikel wordt de meest voorkomende combinatie van Min en Aanvulhoeveelheid de nieuwe Artikel-Kanbanstandaard. Bij een gelijke stand wordt de combinatie met de laagste som `Min + Aanvulhoeveelheid` gekozen; verdere tie-breakers zijn de laagste Min en daarna de laagste Aanvulhoeveelheid. Posities die niet overeenkomen met de gekozen combinatie blijven lokale afwijkingen. Oude waarden die geen geldige Aanvulhoeveelheid opleveren, blokkeren de migratie totdat ze zijn gecorrigeerd.

## Gevolgen

- Nieuwe Artikelen starten met `Min 1 / Aanv. 1`.
- Standaard materiaal heeft geen opgeslagen of zichtbare Kanban-instellingen. Terugschakelen naar Kanban volgt de actuele Artikel-Kanbanstandaard.
- Alleen een afwijkend getal krijgt een subtiele kleur en een klein instellingenicoon; de tooltip toont de Artikel-default. Er komt geen tekstbadge op regelniveau.
- Een wijziging van de effectieve Min of Aanvulhoeveelheid maakt bestaande fysieke kaartjes verouderd, maar start geen automatische print.
- BadgyAutomation ontvangt voor Kanban-kaarten `minLevel` en `refillQuantity`; `maxLevel` verdwijnt. De bestaande Badgy 200-endpoint-, renderer- en printerroute blijven verder functioneel ongewijzigd.
