# Gescheiden printstromen voor Locatiekaarten en Kanban-kaarten

Status: accepted

Locatiekaarten worden via een eigen A4-duplex printcontract en logische printer naar de kleurenprinter gestuurd; Kanban-kaarten blijven via de bestaande Badgy 200-flow lopen. We kiezen deze scheiding omdat beide kaarttypen een ander fysiek formaat, andere printer en andere batchlogica hebben, terwijl de kamerinterface ze bewust als twee eenvoudige acties aanbiedt.

## Consequences

- De beheerapplicatie heeft aparte printopdrachten voor Locatiekaarten en Kanban-kaarten.
- De bestaande `KANBAN_TWO_BIN`-rendering en Badgy 200-printerroute blijven functioneel ongewijzigd.
- BadgyAutomation krijgt een afzonderlijk contract voor A4-vellen met acht dubbelzijdige Locatiekaarten.
- Een kameractie kan alleen de kaarttypen selecteren die de gebruiker heeft aangevinkt; er is geen verplichte gecombineerde printactie.
