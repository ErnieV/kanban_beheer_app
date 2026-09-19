# Vivaldi Kanban

Dit contextdocument beschrijft de domeintermen voor praktijken, toegang, artikelen, voorraadposities, ruimtes, opslaglocaties en fysieke kaartjes binnen Vivaldi Kanban.

## Praktijken en toegang

**Praktijk**:
De klantomgeving en bevoegdheidsgrens waarbinnen vestigingen, gebruikers, ruimtes, opslaglocaties en het lokale assortiment worden beheerd.
_Avoid_: bedrijf wanneer de klantomgeving wordt bedoeld

**Gebruiker**:
Een persoon met één identiteit die toegang kan hebben tot één of meer Praktijken en daar dagelijkse werkzaamheden uitvoert. Een Gebruiker mag Voorraadposities binnen bestaande Ruimtes beheren, waaronder Materiaaltype, Min en Aanvulhoeveelheid, maar wijzigt niet de structuur van Ruimtes of Opslaglocaties.
_Avoid_: assistente als algemene naam voor een gebruiker

**Publieke scan**:
Een scan die iedereen zonder Gebruiker of Praktijklidmaatschap mag uitvoeren om een behoefte vanuit een fysieke kaart direct te melden. De scan registreert de melding zonder aanvullende bevestiging; dit bestaande gedrag blijft voor de overgang behouden.

**Praktijklidmaatschap**:
De koppeling die een Gebruiker toegang geeft tot één Praktijk en diens rol binnen die Praktijk vastlegt. Dezelfde Gebruiker kan per Praktijk een ander Praktijklidmaatschap en een andere rol hebben.

**Uitnodiging**:
Een gerichte verlening van toegang aan een persoon met een vooraf bepaalde rol. Een Platformbeheerder kan Platformbeheerders en leden van iedere Praktijk uitnodigen; een Praktijkbeheerder kan alleen Gebruikers voor de eigen Praktijk uitnodigen. Zonder Uitnodiging ontstaat geen toegang. De ontvanger accepteert de Uitnodiging via een uniforme e-mailaanmelding met verplichte MFA; een werk- of school-entra-aanmelding en een afzonderlijke Google-aanmelding zijn geen zichtbare gebruikersopties.

**Aanmeldherstel**:
Het herstellen van toegang wanneer een Gebruiker een wachtwoord, apparaat of MFA-methode kwijt is. Aanmeldherstel volgt uitsluitend de geconfigureerde policy van de beheerde identiteitsdienst; Vivaldi Kanban kent hiervoor geen eigen herstelroute of praktijkgebonden uitzonderingsrecht.

**Platformbeheerder**:
Een platformbrede beheerder met volledige toegang tot alle Praktijken en alle praktijkgegevens. De Platformbeheerder beheert Praktijken, Platformbeheerders, Praktijkbeheerders en Gebruikers en is verantwoordelijk voor de Platform Catalogus en Platform Templates. Een Praktijklidmaatschap is voor deze toegang niet nodig. Er blijft altijd minstens één Platformbeheerder bestaan.
_Avoid_: hoofdbeheerder

**Praktijkbeheerder**:
Een beheerder die alleen binnen de eigen Praktijk Gebruikers, het Praktijkassortiment, Ruimtes en Opslaglocaties kan beheren. De Praktijkbeheerder maakt, hernoemt, verplaatst en verwijdert de structuur van Ruimtes en Opslaglocaties.

**Platform Catalogus**:
Het platformbreed beheerde aanbod van artikelen waaruit Praktijken hun eigen assortiment kunnen samenstellen. Platformbeheerders beheren deze catalogus.

**Praktijkassortiment**:
De verzameling artikelen die één Praktijk gebruikt. De Praktijkbeheerder selecteert artikelen uit de Platform Catalogus, kan lokale artikelen toevoegen en beheert de lokale Artikel-Kanbanstandaard.

**Artikelupdatevoorstel**:
Een zichtbare melding van één afzonderlijke wijziging in de overeenkomstige inhoud in de Platform Catalogus. De Praktijkbeheerder accepteert of wijst iedere wijziging bewust en apart af; een voorstel voert nooit zelfstandig een wijziging door in het Praktijkassortiment. Een Platformfoto-update is geen voorstel wanneer het Artikel een Aangepaste Artikelfoto heeft.

**Artikelafwijking**:
Een gekleurde markering op een Artikel in het Praktijkassortiment waarvan een Artikelupdatevoorstel is afgewezen en dat afwijkt van de actuele overeenkomstige inhoud in de Platform Catalogus. De markering blijft zichtbaar zolang de afwijking bestaat.

**Ruimteartikelupdatevoorstel**:
Een zichtbare melding op een Artikel bij één Voorraadpositie dat één afzonderlijke wijziging in Mijn Artikelen wacht op acceptatie of afwijzing. Een Gebruiker of Praktijkbeheerder beslist per wijziging; een voorstel verandert de inrichting van de Ruimte nooit zelfstandig. Een wijziging van de Artikelfoto is hiervan uitgezonderd.

**Platform Template**:
Een platformbreed beheerde basisinrichting voor een Ruimtetype. Platformbeheerders beheren deze templates zodat Praktijken ze als herkenbaar uitgangspunt en vergelijkingsbasis kunnen gebruiken.

**Praktijktemplate**:
De zelfstandig beheerde inrichting die één Praktijk voor een Ruimtetype gebruikt. Een Praktijktemplate kan vanuit een Platform Template of volledig binnen de Praktijk worden gemaakt. Latere platformwijzigingen worden nooit in de Praktijktemplate doorgevoerd. Wijzigingen aan een Praktijktemplate worden evenmin automatisch in bestaande Ruimtes doorgevoerd.

**Templateafwijking**:
Een zichtbare markering op een regel van een Praktijktemplate die op dat moment verschilt van de overeenkomstige regel in de Platform Template. De markering is informatief en voert geen wijziging door.

**Inrichtingsafwijking**:
Een zichtbare markering op een regel van een bestaande Ruimte die op dat moment verschilt van de overeenkomstige regel in de gekoppelde Praktijktemplate. De markering is informatief en voert geen wijziging door. Een Ruimte zonder gekoppelde Praktijktemplate heeft geen Inrichtingsafwijkingen.

## Locaties en voorraad

**Vestiging**:
Een fysieke locatie van een Praktijk waar ruimtes en opslaglocaties zijn ingericht.

**Ruimte**:
Een benoemde kamer binnen een vestiging, met een kamertype dat de ruimte visueel kan herkennen. Een Ruimte kan vanuit een Praktijktemplate of volledig zonder template worden ingericht.

**Kamertype**:
Een classificatie van een ruimte met een eigen herkenningskleur.

**Opslaglocatie**:
De laagste benoemde opslagplek binnen een ruimte waar een artikel wordt bewaard. In de huidige fysieke inrichting kan dit bijvoorbeeld een kast of lade zijn.
_Avoid_: kast wanneer de fysieke opslagplek als domeinlocatie wordt bedoeld

**Artikel**:
Een product dat binnen het assortiment van een Praktijk wordt beheerd, inclusief de standaard Kanban-instellingen voor dat artikel.
_Avoid_: product wanneer het om het beheerde assortiment-item gaat

**Artikelnaam**:
De naam waaronder een Artikel in Mijn Artikelen wordt beheerd. Ruimtes en Voorraadposities hebben geen eigen Artikelnaam en tonen daarom automatisch de actuele Artikelnaam uit Mijn Artikelen.

**Aangepaste Artikelfoto**:
Een door een Praktijk gekozen Artikelfoto die de overeenkomstige foto uit de Platform Catalogus vervangt. Een Platformfoto-update wordt niet als voorstel getoond zolang deze eigen foto actief is.

**Artikelfoto**:
De foto die een Artikel identificeert. Ruimtes en Voorraadposities hebben geen eigen Artikelfoto en tonen daarom automatisch de actuele Artikelfoto uit Mijn Artikelen.

**Voorraadpositie**:
De combinatie van één artikel en één opslaglocatie, inclusief het materiaaltype en de voorraadregels die daar gelden.

## Materiaaltypen

**Kanban-materiaal**:
Materiaal op een voorraadpositie waarvoor een Min-niveau, een Aanvulhoeveelheid en een Kanban-kaart gelden.

**Aanvulhoeveelheid**:
Het aantal eenheden dat bij Kanban-materiaal wordt toegevoegd wanneer het Min-niveau is bereikt of onderschreden. De beoogde voorraad na aanvullen is conceptueel Min plus de Aanvulhoeveelheid. Dit is geen absolute doelvoorraad.

**Artikel-Kanbanstandaard**:
De standaardcombinatie van Min en Aanvulhoeveelheid die als bron geldt voor Kanban-posities van één Artikel, tenzij een voorraadpositie daarvan afwijkt. Een wijziging wordt per Voorraadpositie als afzonderlijk wijzigingsvoorstel beoordeeld en nooit automatisch doorgevoerd.

**Voorraadpositie-afwijking**:
Een Min en/of Aanvulhoeveelheid op één voorraadpositie die daadwerkelijk afwijkt van de Artikel-Kanbanstandaard. Een positie met gelijke waarden heeft geen afwijking en volgt de actuele standaard.

**Effectieve Kanban-instelling**:
De Min en Aanvulhoeveelheid die voor één Kanban-voorraadpositie gelden na geaccepteerde wijzigingen uit de Artikel-Kanbanstandaard en een eventuele Voorraadpositie-afwijking.

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

## Foutmeldingen

**Veilige foutmelding**:
Bij een onverwachte fout in een generieke dispatcher-route (één route-handler voor meerdere entiteittypen of acties) toont de gebruiker nooit de ruwe technische foutdetail — geen exceptietekst, URL, database- of HTTP-detail. In plaats daarvan verschijnt een actie-benoemde, generieke melding die qua register aansluit bij de bestaande succesmelding van diezelfde route (bijvoorbeeld "Verwijderd." bij succes naast "Verwijderen is niet gelukt. Er is niets verwijderd. Probeer het later opnieuw, of vraag hulp." bij een onverwachte fout). De technische oorzaak wordt altijd server-side gelogd. Een reeds-bekende, specifieke foutsituatie (bijvoorbeeld een dubbele naam) krijgt een eigen, behulpzame melding vóór deze generieke fallback.
