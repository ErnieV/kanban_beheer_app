# Locatiekaartjes via de Printwachtrij in plaats van direct printen

---
status: accepted
---

Een aangevraagde Locatiekaart-batch wordt niet langer synchroon naar de A4-printservice gestuurd. Net als Kanban-kaartjes belandt een aanvraag eerst in de Printwachtrij; pas een expliciete `Versturen naar printer`-actie stuurt de opdracht daadwerkelijk naar de kleurenprinter. Beide kaartsoorten krijgen zo dezelfde werkstroom — `selecteren → in wachtrij zetten → versturen` — met als enige verschil het eindproduct en de doelprinter. Dit vervangt geen techniek: de scheiding uit [ADR 0001](0001-gescheiden-printstromen.md) tussen de Badgy 200-route voor Kanban-kaarten en het A4-duplexcontract voor Locatiekaarten blijft volledig van kracht. Alleen het moment van versturen verandert.

## Gevolgen

- De Printwachtrij toont voortaan twee secties met identieke opbouw: Kanban-kaartjes (`Print_Queue`, status `PENDING`) en Locatiekaartjes (`LocatiekaartVersie`, status `PENDING_PRINT`). Per sectie is zichtbaar welke fysieke printer de bestemming is.
- Geen schemawijziging nodig: `LocatiekaartVersie` kreeg de status `PENDING_PRINT` al bij het aanmaken van een versie; alleen het moment waarop die naar `PRINTED` gaat verschuift van "bij aanvragen" naar "bij versturen vanuit de wachtrij".
- Aanvragen van Locatiekaartjes maakt of hergebruikt voortaan alleen nog versies en commit die, zonder de A4-printservice aan te roepen. Het batch-id voor de printservice ontstaat pas bij het versturen; de eerder gebruikte `printBatchId`/`printBatchSelection`-velden op het selectiescherm vervallen.
- Een bewust opnieuw geselecteerde, inhoudelijk ongewijzigde Locatiekaart die al `PRINTED` was, wordt expliciet teruggezet naar `PENDING_PRINT`. Zonder deze regel zou "opnieuw aanvragen" van een ongewijzigd kaartje stilzwijgend niets aan de wachtrij toevoegen terwijl de melding succes suggereert.
- Beide secties van de wachtrij bieden zowel een batchactie (`Versturen naar printer`, alles) als een actie per regel. Ook voor Locatiekaartjes: het A4-printcontract kent geen minimum of veelvoud-eis (`sheetCount` is een pure plafondberekening op het aantal kaarten), dus één kaartje versturen levert een geldig vel met lege vakjes op. De interface waarschuwt daar per regel voor, om onbedoeld papierverbruik te voorkomen zonder de functie weg te nemen.
- De succesmelding bij versturen toont het aantal kaartjes en vellen in gewone taal, zonder printBatchId of jobId; die technische identifiers verdwijnen uit de voor de assistente zichtbare tekst.
