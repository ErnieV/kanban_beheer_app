# Vivaldi Kanban

Dit contextdocument beschrijft de domeintermen voor het beheren van artikelen, voorraadposities, kamers, opslaglocaties en fysieke kaartjes binnen Vivaldi Kanban.

## Locaties en voorraad

**Vestiging**:
Een fysieke locatie van de organisatie waar kamers en opslaglocaties zijn ingericht.

**Ruimte**:
Een benoemde kamer binnen een vestiging, met een kamertype dat de ruimte visueel kan herkennen.

**Kamertype**:
Een classificatie van een ruimte met een eigen herkenningskleur.

**Opslaglocatie**:
De laagste benoemde opslagplek binnen een ruimte waar een artikel wordt bewaard. In de huidige fysieke inrichting kan dit bijvoorbeeld een kast of lade zijn.
_Avoid_: kast wanneer de fysieke opslagplek als domeinlocatie wordt bedoeld

**Artikel**:
Een product dat binnen het assortiment van een bedrijf wordt beheerd, inclusief de standaard Kanban-instellingen voor dat artikel.
_Avoid_: product wanneer het om het beheerde assortiment-item gaat

**Voorraadpositie**:
De combinatie van één artikel en één opslaglocatie, inclusief het materiaaltype en de voorraadregels die daar gelden.

## Materiaaltypen

**Kanban-materiaal**:
Materiaal op een voorraadpositie waarvoor een Min-niveau, een Aanvulhoeveelheid en een Kanban-kaart gelden.

**Aanvulhoeveelheid**:
Het aantal eenheden dat bij Kanban-materiaal wordt toegevoegd wanneer het Min-niveau is bereikt of onderschreden. De beoogde voorraad na aanvullen is conceptueel Min plus de Aanvulhoeveelheid. Dit is geen absolute doelvoorraad.

**Artikel-Kanbanstandaard**:
De standaardcombinatie van Min en Aanvulhoeveelheid die geldt voor Kanban-posities van één Artikel, tenzij een voorraadpositie daarvan afwijkt. Een positie zonder afwijking volgt wijzigingen van deze standaard automatisch.

**Voorraadpositie-afwijking**:
Een Min en/of Aanvulhoeveelheid op één voorraadpositie die daadwerkelijk afwijkt van de Artikel-Kanbanstandaard. Een positie met gelijke waarden heeft geen afwijking en volgt de actuele standaard.

**Effectieve Kanban-instelling**:
De Min en Aanvulhoeveelheid die voor één Kanban-voorraadpositie gelden nadat de Artikel-Kanbanstandaard en een eventuele Voorraadpositie-afwijking zijn toegepast.

**Standaard materiaal**:
Materiaal op een voorraadpositie dat op die locatie aanwezig hoort te zijn, zonder Min-, Aanvul- of andere Kanban-regels, voorraad-aantal of Kanban-aanvulling.

**Materiaaltype**:
De keuze tussen Kanban-materiaal en Standaard materiaal voor één voorraadpositie. Hetzelfde artikel kan per voorraadpositie een verschillend materiaaltype hebben.

## Fysieke kaartjes

**Kanban-kaart**:
Een kaartje dat bij Kanban-materiaal hoort en gebruikt wordt voor het bestaande Kanban- en scanproces.

**Locatiekaart**:
Een zelfstandig kaartje dat aan één voorraadpositie is gekoppeld en fysiek bij de opslaglocatie wordt bevestigd. De voorkant identificeert het artikel; de achterkant identificeert de volledige locatie.

**Locatiekaartversie**:
Een concrete inhoudelijke versie van een Locatiekaart. Een wijziging aan het artikel, de locatie of andere afgedrukte inhoud maakt de vorige versie verouderd.

**Printbatch**:
Een door de gebruiker geselecteerde groep kaartjes die als één printopdracht wordt verwerkt.
