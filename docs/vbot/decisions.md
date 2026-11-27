# Decyzje dla DBVictory po audycie vBota

Stan kodu DBVictory `b03ad36`, vBot 4.8 `09d6816`, OTCv8 `aacfe3f`. Ten dokument odróżnia obserwacje w źródłach, odtworzenia z atrapami, pomiar lokalny i propozycje zmian. Audyt dotyczy architektury. Wersja, która udostępnia bazę wiedzy przez MCP, nie zmienia procesu łączenia ani logiki gry. [Pełny katalog](modules.md) · [Integracja z klientem](architecture.md)

[Wdrożenie w v1.26.0](#wdrożenie-sześciu-obszarów-w-v1260) · [Pełny plan przebudowy silnika](#pełny-plan-przebudowy-silnika) · [Pilotaż Hunter Knife i sesji](#plan-wdrożenia-i-kryteria-wydania)

## Pierwsze decyzje i ich koszt

| Kolejność | Decyzja | Efekt dla użytkownika | Warunek przyjęcia |
|---|---|---|---|
| 1 | Wiek ostatniej poprawnej wiadomości w panelu i wznowienie jego lokalnego kanału | Zamiast zamrożonego HP z zielonym statusem użytkownik widzi utratę świeżości i jej powód | Po 15 s ciszy przy nadal otwartym WS panel przechodzi w stan nieaktualny, podejmuje pojedyncze ponowienie i wraca po pierwszym poprawnym odczycie. Podczas recovery pozostają dostępne ręczne zatrzymanie oraz okno gry. |
| 2 | Jeden punkt wznawiania dozwolonych modułów na generację logowania | Stary sygnał logowania nie uruchamia czynności w nowym świecie | A zalogowane → oczekiwanie na stan → wylogowanie A → szybki login B. Stara generacja wywołuje zero startów. Każdy zapisany aktywny moduł wystartuje w B co najwyżej raz po spełnieniu swoich wymagań. |
| 3 | Czasy od decyzji do efektu i przyczyna postoju obok waypointu | Widać, czy po zabiciu moba czekamy na identyfikację ciała, dostępność przedmiotu, wolny ruch lub następną próbkę świata | Histogram p50/p95 z liczbą prób, również dla odmowy, przeterminowania i braku potwierdzenia. Próby ręczne i automatyczne na tym samym typie pola i w takim samym stanie walki. |
| 4 | Bufor opisów akcji i aktualizacja ekwipunku po zdarzeniach | Panel oraz odczyty noża wykonują mniej zbędnej pracy. Zysk dla płynności pozostaje do zmierzenia | Opisy odświeżają się po zmianie plików. W teście na sesji kontrolowanej zmierzyć opóźnienie pętli silnika, obciążenie i wiek danych z 0, 1 i 2 odbiorcami. Weryfikacja przed użyciem przedmiotu nadal rozpoznaje zmianę slotu i relog. |
| 5 | Osobny eksperyment z interfejsem klienta udostępnionym przez producenta | Jeden Start mógłby otworzyć oba okna, a potem wiarygodnie pokazać gotowość po zalogowaniu, również gdy gracz jest już online | Najpierw dokumentacja lub potwierdzenie obsługiwanych sygnałów dla tej wersji DBVictory. Później próba wyłącznie odczytowa, porównanie pozycji i HP z grą, menu → login → relog → utrata połączenia. |

Kolejność 1–3 daje widoczną poprawę diagnozy nawet wtedy, gdy nie można zmienić integracji z klientem. Punkt 4 ma najmniejszy zakres jako samodzielna optymalizacja panelu. W pierwszym wydaniu warto oddzielić poprawkę wiarygodności statusu od zmian podejmujących akcje w grze.

## Start i połączenie w obecnej instalacji

| Sytuacja | Potwierdzone dzisiaj | Zalecenie |
|---|---|---|
| Właściwy klient jest już otwarty od początku kontroli launchera | Jeden Start wystarcza na dalszy przebieg launchera, gdy pozostaje to ten sam proces. | Pokazywać kolejne znaczące stany oraz czas od ostatniego potwierdzenia. |
| Launcher sam uruchamia klienta albo klient został zastąpiony innym procesem | Obecny przebieg oczekuje osobnego potwierdzenia. Wykryte okno dowodzi obecności klienta. | Jeden przycisk może już zainicjować otwieranie aplikacji i panel oczekiwania. Automatyczne potwierdzenie faktycznej gotowości wymaga obserwowalnego zdarzenia wspieranego przez klienta. Stałe dwie sekundy nie są dowodem. |
| Otwarty panel i brak danych postaci | Gotowość okna Electron jest oddzielna od zalogowania oraz aktualności mapy, HP i pozycji. | Po uruchomieniu wyświetlać „Zaloguj się w grze”, po logowaniu „Czekam na pozycję i mapę”, potem „Połączono”. Przez cały czas utrzymać odrębny sygnał dostępności panelu. |
| Użytkownik jest już w świecie gry | Host OTCv8 podczas inicjalizacji sprawdza stan online. W DBVictory brak potwierdzonego oficjalnego odpowiednika z kompletną migawką wejściową. | W wariancie z obsługiwanym adapterem oczekiwać migawki aktualnej generacji. Po luce uzgodnić najnowszy stan i zachować historię potwierdzeń. |
| Gra, klient lub lokalny panel się rozłączyły | To trzy różne awarie. Istnieją testy odrębnego procesu transportu na loopback; nie dowodzą bezprzerwowego restartu na produkcyjnym połączeniu. | Ponowienie połączenia z samym lokalnym panelem jest oddzielną operacją od wznowienia sesji gry. Po zmianie sesji odrzucić stare oczekujące decyzje. |

W domyślnej instalacji klienta `DBVClient_v5` sprawdzono nazwy luźnych manifestów i dostępnych plików pomocy. Wśród 60 manifestów są m.in. battle, loot i playerdeath. Nie znaleziono modułu `game_bot` ani publicznej dokumentacji API rozszerzeń w tej części instalacji. To ograniczony wynik inspekcji, a nie rozstrzygnięcie o możliwościach producenta lub zawartości spakowanych zasobów. Testy JS na atrapach pokryły pięć przypadków: wybór instalacji, zimny start, już otwarty klient, podmiana procesu i zamknięcie okna podczas startu. Wszystkie przeszły. Logowanie do serwera pozostaje oddzielnym testem. [Launcher](../../launcher/desktop/main.js) · [Identyfikacja okna](../../launcher/desktop/host.js) · [Testy launchera](../../tests/desktop_launcher.test.cjs) · [Cykl hosta OTCv8](https://github.com/OTCv8/otclientv8/blob/aacfe3f1fe4bcadb5a34ff1f36263e1c96b3dd32/modules/game_bot/bot.lua#L20-L73)

Strona pobierania producenta opisuje oficjalny klient i wariant uruchomienia graficznego. Nie dokumentuje sygnału gotowości ani SDK modułów. W komunikacie #102 z 16 listopada producent wymienia zmiany zachowania follow przy ręcznym ruchu podczas ataku i opóźnień minimapy przy zmianie kondygnacji z włączoną mgłą. Te przypadki warto rozróżnić w odtworzeniach od czasu odpowiedzi naszego panelu. Producent opisuje też egzekwowanie ograniczeń używania botów, więc dostępność publicznego OTCv8 nie oznacza wsparcia vBota w oficjalnym wydaniu gry. Oddzielnym źródłem pomocnym przy weryfikacji Atlasu jest oficjalna interaktywna mapa wysp, potworów i NPC. Sama mapa nie potwierdza współrzędnych aktualnej grupy, spawn timera ani drożności schodów. [Klient producenta](https://dbvictory.eu/download.html) · [Komunikat DBVictory #102](https://dbv.dbvictory.eu/) · [Mapa producenta](https://dbv.dbvictory.eu/map.html)

## Co już odtworzyliśmy

**Panel może zachować stary odczyt jako aktywny.** W izolowanym teście rzeczywistego hooka React i stanu sesji dostarczono pierwszą próbkę HP 75, pozostawiono logicznie otwarty WebSocket i przesunięto zegar o 15 s bez kolejnych ramek. Wynik: połączenie panelu nadal aktywne, HP 75, statystyki aktywne, zero odczytów awaryjnych HTTP i zero oczekujących ponowień. Funkcja aktualnie pomija fallback, gdy transport ma stan OPEN. To powtarzalny przypadek UI, bez ustalenia przyczyny konkretnych dawnych postojów w grze. [useBot](../../dashboard/lib/useBot.ts) · [Status silnika](../../dashboard_api.py)

**Rozłączenie podczas inicjalizacji.** W próbie na istniejącej funkcji oczekującej po sygnale logowania zasymulowano rozłączenie przed uzyskaniem gotowości. Callback uruchamiania listy akcji został wywołany jeden raz mimo braku aktualnego logowania. Nie wysłano komendy do gry. Drugie miejsce startu modułów i kontrola już działającego zadania ograniczają duplikaty, ale same nie rozwiązują wyścigu między generacjami. Rozwiązanie powinno obejmować wybudzenie po właściwej próbce i odrzucenie starej generacji tuż przed uruchomieniem modułu. [Silnik i obsługa logowania](../../mcp_server.py) · [Tożsamość po relogu](../../tests/test_relogin_identity.py) · [Dostarczenie komend](../../command_delivery.py)

**Koszt powtarzanego odczytu opisów w panelu.** Przy każdym pełnym statusie na badanej rewizji silnik wylicza katalog i ponownie otwiera do 23 plików akcji. Na tym komputerze rozgrzano cache 10 przebiegami, a następnie zmierzono 300 przebiegów, po 23 opisy w każdym. Odczytano 157 266 bajtów tekstu na przebieg. Mediana wyniosła **2,568 ms**, p95 **3,110 ms**. Druga niezależna seria 150 powtórzeń dała 2,76 / 3,56 ms. Przy nominalnym nadawaniu co 100 ms jest to usuwalny koszt. System operacyjny może obsługiwać odczyty z pamięci. Nie zmierzono tu zapisu na dysk, CPU klienta ani czasu kroku postaci. Samo kodowanie całej mapy, liczby odbiorców i koszt WS nie są częścią tego mikrobenchmarku. [Budowa statusu i rozsyłanie](../../dashboard_api.py)

## Rekomendacje dla poszczególnych mechanik

| Obszar | Co przenieść z analizy | Co zachować lub rozwinąć po naszej stronie |
|---|---|---|
| Ruch i zablokowany waypoint | Wybudzanie po zmianie pozycji i parametr czasu kroku | Osobne budżety dla czekania na pole, pozycję i ponowny plan. Korekta po ręcznym ruchu na tym samym piętrze, obowiązkowe potwierdzenia schodów i odtworzenie aktualnego etapu. |
| Lurowanie | Krótko ważne prawo do kierowania ruchem konkurujących modułów | Mierzyć osiągalne miejsca dla grupy, szerokość korytarza i utratę kontaktu z wolniejszymi potworami. Nadawać wygaśnięcie decyzji o postoju i wypisywać jej autora. |
| Cel i czary | Rozdzielić wybór celu, obserwowane zaznaczenie i sprawdzanie cooldownu | Potwierdzać cel w tej samej generacji sesji. Rozróżnić chwilowy brak kontaktu, śmierć, utratę celu i zmianę piętra. Dla cooldownu rozróżnić wartość obserwowaną i oszacowaną. |
| Hunter Knife, loot, pajęczyny, AiO | Kierować pracę na zmianę odpowiedniego pola, stosu lub pojemnika | Własność ciała, odmowa, stary stos, nowe ciało i wysłana komenda mają oddzielne identyfikatory lub dowody. Najpierw mierzyć opóźnienie przed kolejną korektą progów. |
| Mikstury, Speed Up, band | Reguły potrzebują tylko swoich danych i wspólnych ograniczeń gry | Krytyczne leczenie ma działać przy braku mapy wymaganej przez ruch i przy zajętym polu. Utrzymać dziennik niepewnego wyniku oraz współdzielony cooldown podczas recovery. |
| Atlas, pętle, zapis adaptacyjny | Rozrzedzanie zwykłych fragmentów i osobne akcje na przejściach | Zachować skorygowane pokrycie sektorów, początek kontaktu z mobem, powrót między piętrami, unikanie niepotrzebnego powtórzenia korytarza. Źródłem prawdy jest symulacja geometrii i przejść. |
| Alarmy i diagnostyka | Zdarzenia gracza, wiadomości i stanu mogą mieć osobnych odbiorców | Ślad przyczyny, wieku, potwierdzenia i wyciszenia. Test wielu odbiorców i pojawienia się gracza przy aktywnym powiadomieniu. |
| Profile, analityka i rozszerzalność | Jawne domyślne profile i listy reguł, przypisanie opcji do konkretnego modułu | Atomowy zapis właściwej domeny, wersjonowanie schematu, niezmienne ID trasy i wiązanie historii z aktualną postacią. Ładowanie katalogu, MCP lub podglądu ma wysłać zero komend do gry. |

W praktyce mobilizacja wszystkich modułów co 10 ms nie jest warunkiem płynności. Warto wybudzać domenę na zmianie danych, mierzyć czas oczekiwania i mieć rzadką próbę uzgodnienia stanu, gdy zgubiono zdarzenie. Upstream ogranicza makra do 50 ms, a nawet w nim niskie timeouty nie gwarantują odpowiedzi. [Uruchamianie makr](https://github.com/OTCv8/otclientv8/blob/aacfe3f1fe4bcadb5a34ff1f36263e1c96b3dd32/modules/game_bot/functions/main.lua#L33-L48) · [Domeny DBVictory](../../automation_domains.py)

## Jak potwierdzać poprawę bez przypisywania winy serwerowi

1. Każdą próbę korelować z sesją, inicjatorem, konkretnym etapem trasy lub obiektem oraz typem oczekiwanego potwierdzenia. Używać istniejącego `CommandReceipt` z informacją o przekazaniu oraz `movement_history`. Po utracie sesji zakończyć oczekującą obserwację jako nieustaloną. [Transport i efekt](../../command_delivery.py) · [Historia kroku](../../movement_history.py)
2. Dla noża rozdzielić czasy od zmiany pola do rozpoznania, wyboru, przekazania do transportu, stwierdzonego wysłania i pierwszej próbki potwierdzającej efekt. Obecne `last_timing_ms` w v1.24.4 obejmuje stan po udanej próbie. Znacznik początku trzeciej fazy powstaje przy wyborze próby. Obejmuje zatem także warunki po stronie bota. Nawet przedział po wysłaniu może obejmować dostarczenie następnej klatki mapy. [Hunter Knife](../../hunter_knife_action.py)
3. Testy odtworzeniowe rozdzielić na dwie identyczne warstwy na polu, odmowę dla obcej próby, zniknięcie potwora bez przyrostu XP obok starego ciała, blokadę schodów z aktywnym leczeniem, zmianę piętra w trakcie oczekiwania, ręczny krok i nagłe zerwanie źródła danych. W decyzjach raportować również pominięcia i brak danych. [Kolejka ciał](../../hunter_knife.py) · [Historia decyzji](../../cavebot_history.py) · [Odzyskiwanie sesji](../../engine_recovery.py)
4. Oddzielić zalety architektury od zgodności z konkretną grą. W vBocie próg alarmu many korzysta w jednej gałęzi z HP, funkcje przełącznika HealBota zapisują profil ataku, a leczenie przedmiotem może być opóźniane podczas lootu. Z tego wynikają trzy testy domenowe: niska mana przy pełnym HP, zapis profilu leczenia bez zmiany walki i natychmiastowa szansa na leczenie przy aktywnym loocie. [Alarmy](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/alarms.lua#L179-L190) · [Zapis leczenia](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/HealBot.lua#L513-L523) · [Leczenie i loot](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/HealBot.lua#L621-L647)

**Decyzja końcowa.** Zachować działający silnik i potwierdzenia DBVictory. Najwięcej przemawia za dokładniejszym stanem gotowości, usunięciem podwójnej inicjalizacji oraz budzeniem domen po obserwacjach. Rozważyć inny model połączenia dopiero po potwierdzeniu, jaki kontrakt faktycznie udostępnia producent klienta. Ani obecność otwartego okna, ani zgodność rodziny silnika gry sama tego nie rozstrzyga.

## Plan wdrożenia i kryteria wydania

Punktem odniesienia jest v1.25.0 (`b7706c1`). Ten pilotaż obejmuje Hunter Knife i gotowość sesji. W logice noża udało się powtórzyć dwie konkretne usterki. Poniższe czasy pochodzą z wirtualnego zegara i określonych sekwencji wejściowych. Kryteria pilotażu są propozycją do oceny przyszłych wydań.

### Etapy

| Etap | Zmiana i zależności | Kiedy można wydać |
|---|---|---|
| 1. Hunter Knife +1 i ruch | Oddzielić przypisanie własnego ciała od pojedynczej próby użycia. Po pewnym niewysłaniu ponownie sprawdzić zasięg, tożsamość sesji, bieżącą warstwę i położenie noża. Aktywny cel może nadal otrzymywać ataki. Krok utrzymujący zasięg może się odbyć. Przypadek kroku oddalającego obsłużyć krótkim odroczeniem ruchu lub przy pierwszym potwierdzonym powrocie do zasięgu. Nie prowadzić postaci za znikającym ciałem. | Dokładnie jedno potwierdzone użycie na ciało w kontroli, po kroku w bok i po powrocie. Zero prób spoza zasięgu. Przy niepewnym wyniku najpierw uzgodnić stan pola. Odmowa własności lub zmiana sesji kończy próbę. |
| 2. Grupy ciał i pomiar całej próby | Zatrzymać fakty o śmierci, ciele i osobistym Loot do końca okna korelacji. Brak kompletu dowodów nie musi zatrzymywać postaci przez cały ten czas. Rozróżnić moment wyboru, faktycznego przekazania, rezultatu transportu i zmiany widocznego stosu. Oba pierwsze etapy można połączyć w jedno wydanie noża. | Wszystkie pięć znanych niepowodzeń przechodzi w trybie akceptacyjnym. Dwie zgodne wiadomości pozwalają rozpoznać dwa własne ciała. Niepełne, spóźnione poza okno lub sprzeczne dowody nie dają prawa do użycia. Stare ciało, brak dropu, odmowa i timeout mają osobne wyniki. |
| 3. Świeżość panelu | Odróżnić otwarty kanał od aktualnej wiadomości. Po ciszy oznaczyć dane jako stare, uruchomić pojedynczy odczyt awaryjny lub ponowne połączenie panelu. Anulować zbędne próby po poprawnej ramce. Niezależny etap od zmian mechaniki gry. | Zdrowy strumień nie generuje prób awaryjnych. Milczący kanał przed pierwszą ramką i po niej odzyskuje łączność lub pokazuje powód. Spóźnione odpowiedzi nie nadpisują świeższego stanu. |
| 4. Jedna aktywacja modułów na logowanie | Połączyć dwie ścieżki startu w jednym koordynatorze. Powiązać każdą oczekującą pracę z generacją sesji i wymaganiami konkretnej domeny. Analiza i pomiary nie włączają zapisanych akcji. | Gotowa sesja uruchamia listę dozwolonych akcji raz. Rozłączenie, brak gotowości i stary callback po podmianie sesji dają zero uruchomień. Blokada ruchu lub brak aktualnej mapy nie zatrzymują oceny leczenia przy świeżym HP. |
| 5. Koszt odczytów i start klienta | Najpierw bufor opisów akcji i wiarygodność indeksu ekwipunku. Porównać 0, 1 i 2 okna panelu przy takim samym odtworzeniu. Po etapach 3–4 pokazać w launcherze osobno okno klienta, gotowość panelu, logowanie i świeże dane. Możliwość oficjalnego połączenia z grą już w toku sprawdzać oddzielnie. | Brak starych opisów po edycji i brak użycia poprzedniego slotu po przełożeniu noża. Zimny start, otwarty klient i relog mają osobne wyniki. Zielony status wymaga świeżych danych właściwej sesji. |

### Co pokazują nowe symulacje

| Wejście do próby offline | Wynik dla v1.25.0 | Znaczenie |
|---|---|---|
| Aktywna walka z następnym mobem, własne odsłonięte ciało obok, postać stoi | Jedna próba wysłana do lokalnej atrapy i jedna potwierdzona zmiana ciała | Kontrola pozytywna dla tej samej sekwencji zabicia oraz XP. |
| Jak wyżej, ale krok przed końcowym sprawdzeniem, wciąż 1 SQM od ciała | Próba odrzucona przed wysłaniem, przypisanie skasowane, zero oskórowań | Powtarzalne pominięcie leży w obsłudze ruchu. Serwer nie brał udziału w tym wyniku. |
| Krok poza zasięg i powrót po 700 ms od początku scenariusza | Brak wysłania poza zasięgiem, a po powrocie nadal zero oskórowań | Należy zachować informację o własności wystarczająco długo, by ocenić możliwość użycia po powrocie. |
| Dwa lub cztery świeże ciała na jednej kratce, osobisty Loot kompletny po 100 ms | Rozpoznanie całej grupy po 300 ms przy klatkach co 50 lub 100 ms i po 500 ms przy klatkach co 250 ms | Mierzony etap kończy się na rozpoznaniu. Nie obejmuje użycia, cooldownu ani kolejnego kroku. |
| Dwa potwierdzone zgony i dwa nowe ciała, drugi osobisty Loot po 1200 ms | 0 rozpoznanych w każdej z trzech częstotliwości mapy. Przed drugim wpisem kolejki śmierci, pojawień i pierwszego Loot są już puste | Komplet do skojarzenia przychodzi wewnątrz okna 1500 ms. Kandydat został wcześniej wyczyszczony. |
| Dwa zgony i ciała, ale tylko jeden własny Loot lub drugi po 1800 ms | Brak przypisania w każdym wariancie | Zachować tę ostrożność także po naprawie opóźnionych poprawnych wpisów. |
| Nieustalony wynik wysłania, zmiana ciała po 600 ms albo brak późniejszej zmiany | Jedna próba w każdym przebiegu. Zmiana po 600 ms dała jedno potwierdzenie, brak zmiany zakończył się bez potwierdzenia. | Sam brak zmiany w pojedynczej klatce nie pozwala wysłać noża ponownie. |
| Panel, pięć przebiegów z wirtualnym zegarem | Ruchomy strumień odświeża HP. Jawne zamknięcie uruchamia próbę odczytu HTTP. Otwarty kanał milczący przez 15 s nie podejmuje żadnej próby, także przed pierwszą ramką | Uzasadnia etap 3 niezależnie od stanu serwera gry. |
| Logowanie, pięć przebiegów z obydwoma callbackami | Gotowość natychmiastowa/opóźniona daje po dwa wywołania procedury startu listy. Rozłączenie lub zastąpienie A przez B daje po jednym starym wywołaniu. Brak gotowego wyjścia transportu daje dwa | Odbiorcą był licznik procedury. Nie ustalono wysłania podwójnych komend do gry. |

### Jak uruchomić ponownie

Polecenia uruchamia się z głównego katalogu kopii roboczej, korzystając ze środowiska Pythona projektu i Node z zależnościami panelu. Atrapy mapy, zegara, logowania i odbiorcy działają lokalnie. Zestaw akceptacyjny ma początkowo ujawniać usterki.

```powershell
python -m pytest -q -rx tests/test_hunter_knife.py tests/test_hunter_knife_movement_replay.py tests/test_hunter_knife_group_replay.py
python -m pytest -q --runxfail tests/test_hunter_knife_movement_replay.py tests/test_hunter_knife_group_replay.py
node tests/simulations/panel_readiness.cjs --acceptance
python tests/simulations/session_readiness.py --acceptance
```

Bez `--acceptance` dwie ostatnie symulacje wypisują przebieg także dla obecnej wersji. Po przebudowie ścieżek logowania trzeba połączyć ich wejścia testowe z nowym koordynatorem i zachować te same zdarzenia i oczekiwane wyniki. Testy izolacji walki, ruchu i leczenia oraz odzyskania waypointu pozostają częścią pełnej regresji. [Ruch przy ciele](../../tests/test_hunter_knife_movement_replay.py) · [Serie i spóźnione dowody](../../tests/test_hunter_knife_group_replay.py) · [Milczący panel](../../tests/simulations/panel_readiness.cjs) · [Zmiana sesji](../../tests/simulations/session_readiness.py) · [Niezależne domeny](../../tests/test_automation_domains.py)

### Pilotaż i wycofanie

W 699 raportach ruchu, 23 nagraniach i 173 raportach okrążeń nie znaleziono ani jednej próby z kompletem czasów od rozpoznania do efektu. W 7 okrążeniach znaleziono 36 wpisów postojów wzmiankujących Hunter Knife. Nie pozwalają one policzyć skuteczności noża ani czasu serwera.

Przy pierwszym pilotażu zebrać co najmniej po 30 jednoznacznych własnych ciał dla gry ręcznej i Cavebota w podobnych warunkach. Osobno podać ciała dostępne, pominięte po ruchu, przypisane niejednoznacznie, próby wysłane, potwierdzone i odmowy. Porównać medianę, p95 i liczbę próbek dla czasu od gotowego ciała do próby oraz od potwierdzenia do następnego kroku. Schowane ciała i utrata zasięgu wymagają odrębnych grup. Liczba 30 jest propozycją na pierwszy pomiar. Nie przyjmujemy czasu serwerowego z symulacji.

Wydawać etapy oddzielnie. Dla etapu noża warunkiem przejścia są zielone testy akceptacyjne, brak podwójnych użyć oraz poprawne zachowanie przy odmowie i nieznanym wyniku. Jeśli wystąpi powtórzenie po niepewnym wysłaniu albo użycie na niejednoznacznym ciele, zatrzymać pilotaż modułu Hunter Knife, zachować raport i wrócić do poprzedniego przetestowanego wydania przy najbliższym bezpiecznym starcie. Wyników pilotażu nie należy wyprowadzać z samego faktu, że launcher zainstalował aktualizację.

## Pełny plan przebudowy silnika

Hunter Knife jest pierwszym przypadkiem użycia wspólnej obsługi zdarzeń. Poniższa kolejność dotyczy całego silnika. Ze wzorca vBota warto przenieść możliwość obudzenia modułu po zmianie w świecie. DBVictory ma już obserwator, stan postaci, potwierdzenia komend, osobne zadania walki i ruchu oraz historię Atlasu. Zmiany można wprowadzać po jednym odbiorcy przy zachowaniu starych szkiców i profili. [Audyt hosta OTCv8](architecture.md) · [Katalog modułów](modules.md)

| Etap | Co zmieniamy | Próba, która rozstrzyga o gotowości |
|---|---|---|
| 1. Sesja i zdarzenia z obecnych źródeł | Spisać dla każdej rodziny danych jej źródło, dokładność czasu, warunek świeżości i sposób odzyskania po luce. Zacząć od HP/Ki, pozycji, zmiany pola i stosu, stworzeń, celu, anulowania ruchu, wiadomości gry i Loot. Opisywać przychodzące zmiany oraz pełne odczyty z użyciem wspólnej generacji sesji, kolejności w obrębie źródła i czasu obserwacji. Po luce unieważniać tylko zależne wnioski. | Wprowadzić brak odczytu, częściową ramkę, spóźniony wynik i szybki relog. Pozycja względna czeka na pozycję absolutną. Świeże HP może nadal wybudzić ocenę leczenia. Stara sesja nie podmienia danych nowej. |
| 2. Stworzenia i obraz mapy | Uzgodnić wpisy mapy, listy stworzeń i zmian HP z bieżącym stanem. Rozróżnić pierwsze pojawienie, przesunięcie, utratę kontaktu, ponowne dostrzeżenie i potwierdzoną śmierć. Zachować typ nieustalony i źródło klasyfikacji. Ograniczyć tożsamość do życia stworzenia w danej sesji. | Pet i dziki Bills na jednej kratce, luka na liście, ponownie użyty ID oraz zniknięcie bez potwierdzonej śmierci. Walka, Atlas i alerty zachowują zgodne klasyfikacje. Pet zajmuje pole i nie powiększa grupy do lurowania. |
| 3. Akcje i automatyka | Rozwinąć istniejący rejestr komend. Osobno zapisać zamiar, przekazanie i obserwowany skutek. Zdarzenia ręczne mają własne źródło, a niejednoznaczny skutek pozostaje bez przypisania. Pierwszy przekrój obejmuje ruch przy ciele i Hunter Knife. Następnie mikstury HP/Ki, Barrier Band, Speed Up, czary i zmiany ekwipunku. Budzić odbiorców po zmianie stanu i po upływie właściwego cooldownu. | Niepewny wynik, przełożenie przedmiotu w plecaku, odnowienie efektu ręcznie i kilka ciał na polu. Zero podwójnych użyć, zero użyć na cudzym lub niejednoznacznym ciele. Osobne czasy od obserwacji do decyzji oraz od wysłania do potwierdzenia. |
| 4. Cavebot, walka i lurowanie | Podłączyć niezależne decyzje ruchu, walki i leczenia do uzgodnionego stanu. Zachować jednego wykonawcę ruchu. Dla oczekiwania zapisać przyczynę, zdarzenie pozwalające ruszyć i termin ponownej oceny. Rozdzielić krok, zmianę piętra, zaliczenie waypointu i domknięcie pętli. W walce używać aktualnej osiągalności i wiedzy o grupie. | Anulowany krok, ręczne przesunięcie, schody, zamknięte drzwi, wolny mob i grupa przed przewężeniem. Mierzyć powrót na trasę, brak powtórzenia kroku, ciągłość leczenia i osiągalnej walki. Na obiegu porównać też miejsce postoju i zgodność licznika okrążeń. |
| 5. Atlas, nagrywanie i raporty | Zapisywać pierwsze spotkanie, dalszą drogę moba, wynik walki oraz XP jako oddzielne fakty. Aktualizować zmienione sektory. Używać tych samych faktów przy szacowaniu spawnu, planowaniu zaznaczonych pięter i raportowaniu XP/Senzu na okrążenie. | Przeciągnąć moba z sali do korytarza. Pierwszy kontakt pozostaje w sali, śmierć w korytarzu, ponowna próbka nie podwaja wyniku. Dla trasy wielopiętrowej sprawdzić pokrycie, potwierdzone schody oraz powrót. |
| 6. Panel i launcher | Wiek danych i generację sesji pokazywać już od pierwszego etapu. Na końcu spiąć gotowość klienta, panelu i domen oraz zapisane profile w jeden czytelny przebieg startu. | Zimny start, klient już otwarty, utrata samego panelu i szybki relog. Stary odczyt nie może oznaczać gotowości. Każdy włączony moduł rozpoczyna pracę najwyżej raz na właściwą sesję. |

### Co znaczy pokrycie danych z pakietów

Aktualny obserwator rozróżnia m.in. pominiętą kopię i zastosowany odczyt częściowy. Liczba zastosowanych ramek nie dowodzi, że odczytano wszystkie zawarte w nich zdarzenia. W pierwszym etapie powstanie tabela rodzin obserwacji z przykładami rozpoznanych skutków i luk. W raporcie dla sesji pokazać liczniki obserwatora, wiek ostatniej poprawnej próbki każdej rodziny oraz zakres utraconego zaufania. Brak informacji o nieznanym zdarzeniu pozostaje niewiadomą. Wystarczą liczniki, znaczniki czasu i przyczyny. [Obserwator i testy](../../tests/test_packet_observer.py) · [Stan postaci](../../client_player_state.py)

### Jak przełączać moduły i co mierzyć

Odtworzyć ten sam ślad z tym samym zegarem i ustawieniami w dotychczasowym i nowym odbiorcy. Nowy odbiorca najpierw tylko raportuje decyzje. Porównać przypisanie zdarzenia, decyzję, czas do decyzji p50/p95, liczbę prób i potwierdzony efekt. Osobno policzyć brak decyzji, odmowy i wygasłe próby, aby krótsze czasy nie ukryły pominiętych akcji. Sprawdzić też p95 opóźnienia pętli silnika i maksymalną kolejkę podczas dużej liczby zmian mapy. Przełączać pojedynczą domenę dopiero po wyjaśnieniu różnic. W każdym przebiegu jedna ścieżka wykonuje akcje. Cofnięcie zmiany domeny ma zachować profil, zapisany szkic i potwierdzony postęp.

Pierwszy nowy test przekrojowy powinien objąć jedną chronologię. Ręczny krok przy własnym ciele, utrata jednej próbki mapy, aktualizacja HP, odbudowa pozycji, relog i spóźnione potwierdzenie poprzedniej sesji. Sprawdzić, czy właściwa generacja zachowuje prawo do ciała w zasięgu, czy leczenie reaguje na aktualne HP, oraz czy po relogu wygasają stare akcje i dowody własności. Następnie dołączyć zapisane obiegi Saiyan – Vegeta East (powrót pętli), Ultra Gotrunks (schody) i Mutated Namekjin (obejście po anulowaniu kroku). To próby zaplanowane dla porównania implementacji.

### Stan początkowy do porównań

Na źródłach `5255504` uruchomiono cztery niezależne zestawy offline. Wyniki dotyczą dotychczasowej implementacji. Zestaw odtworzenia restartu używa lokalnych procesów z syntetycznym stanem gry.

| Zestaw | Wynik | Moduły testowe w katalogu `tests` |
|---|---|---|
| Odczyt, kolejność, dostarczenie i relog | 100 zaliczonych | `test_packet_observer`, `test_command_delivery`, `test_engine_restart_recovery`, `test_relogin_identity` |
| Stworzenia, nagrywanie, trasy i raporty | 67 zaliczonych | `test_pet_tracking`, `test_adaptive_recording`, `test_hunt_areas`, `test_hunt_routes`, `test_cavebot_reports` |
| Cavebot, walka, lurowanie, Hunter Knife | 124 zaliczone, 5 oczekiwanych niepowodzeń | `test_automation_domains`, `test_v2_combat`, `test_lure_reachability`, `test_lure_origin_recovery`, `test_lure_positioning`, `test_hunter_knife`, `test_hunter_knife_movement_replay`, `test_hunter_knife_group_replay` |
| Automatyka, alerty, stan postaci, przejścia i mapa | 221 zaliczonych | `test_potions`, `test_barrier_band`, `test_speed_up`, `test_game_alerts`, `test_transition_learning`, `test_player_updates`, `test_world_minimap` |

Pięć oczekiwanych niepowodzeń dotyczy dwóch błędów opisanych w pilotażu noża. Łącznie przeszło 512 testów. Pomiar opóźnień w działającej grze wymaga oddzielnej próby po wdrożeniu odpowiedniej domeny. [Potwierdzenia komend](../../command_delivery.py) · [Recovery po restarcie](../../tests/test_engine_restart_recovery.py) · [Współpraca ruchu, walki i leczenia](../../tests/test_automation_domains.py) · [Pierwsze spotkanie w Atlasie](../../hunt_sightings.py)

## Wdrożenie sześciu obszarów w v1.26.0

Jedna generacja logowania ma jeden rejestr obserwacji i jedną inicjalizację zapisanych modułów. Obserwacja ma źródło, czas monotoniczny i numer zmiany. Moduł odczytuje numer interesujących go rodzin przed oceną stanu. Jeżeli w tym czasie pojawi się nowy odczyt, kolejne oczekiwanie może zakończyć się od razu. Termin kontrolny uwzględnia cooldowny i źródła, które chwilowo milczą. [Rejestr zdarzeń](../../session_observations.py) · [Logowanie](../../session_startup.py)

| Rodzina | Źródło i interpretacja w diagnostyce |
|---|---|
| HP/Ki i pozycja | Potwierdzony odczyt postaci ma próg świeżości 0,5 s. Gdy używany jest odczyt serwerowy, próg informacyjny wynosi 2 s. Luka w jednym źródle nie kasuje świeżej obserwacji innego źródła. |
| Mapa i stworzenia | Gotowa próbka mapy zawiera pola i stosy stworzeń. Obie rodziny są oznaczane jako stare po 2 s. Ruch nadal sprawdza zgodność środka mapy i własne, krótsze okno tuż przed krokiem. |
| Cel i ekwipunek | Zaobserwowany wybór celu lub czynność zmieniająca ekwipunek sygnalizują potrzebę ponownej oceny. Przy użyciu przedmiotu pracownik sprawdza jego bieżący slot. |
| Wiadomości, Loot, anulowanie kroku, efekty i śmierć | Rejestr przechowuje czas i źródło ostatniego sygnału. Po luce oznacza zależne informacje z danego źródła. O utrzymywaniu się konkretnego efektu decyduje właściwy moduł na podstawie stanu gry. |

Dla komend można odczytać czas od zgłoszenia do wyniku wysłania oraz do pierwszego zaobserwowanego potwierdzenia po tym wyniku. Powtórzona obserwacja zachowuje pierwotny czas i dowód. Ręczne użycie noża w tym samym oknie co próba automatyczna pozostawia skutek bez przypisania. [Potwierdzenia](../../command_delivery.py) · [Hunter Knife](../../hunter_knife_action.py)

Próba przekrojowa odtwarza krok przy przypisanym ciele, brak świeżej mapy przy aktualnym HP, późniejszy odczyt pola, relog i opóźniony wynik starej sesji. Próba logowania obejmuje gotowość natychmiastową, opóźnioną, brak gotowego wyjścia, wylogowanie i zastąpienie postaci. [Próba kilku domen](../../tests/test_session_domain_replay.py) · [Symulacja logowania](../../tests/simulations/session_readiness.py) · [Symulacja panelu](../../tests/simulations/panel_readiness.cjs)

W dwóch próbach na wersję, po 32 syntetyczne kroki, p95 od wysłania kroku do zgodnej mapy wyniosło 47,27 ms na bazie i 47,90 ms po zmianie. Samo oczekiwanie na mapę miało p95 16,13 ms w obu wersjach. Przy blokadzie doszły do skutku wszystkie 32 zaplanowane leczenia. P95 od obniżenia HP do lokalnej próby leczenia wyniosło odpowiednio 47,23 i 16,62 ms. Wyniki zależą od timera Windows i dotyczą lokalnego wykonania z kontrolowanym wejściem. [Obciążenie porównawcze](../../tests/engine_timing_probe.py)
