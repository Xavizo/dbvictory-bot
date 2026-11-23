# Katalog wszystkich plików vBot 4.8

Źródłem jest kompletne drzewo rewizji `09d6816c817f881735ecf78e87e052fcf7c02816`. 71 skryptów Lua, 21 układów OTUI i jeden plik wersji dają 93 pliki. Podczas zwykłego uruchomienia loader sięga 68 skryptów razem z samym loaderem. Status „ładowany” obejmuje również rejestrację funkcji i opcji domyślnie wyłączonych. Nie dowodzi, że dana czynność odbywa się przy każdym logowaniu. [Architektura hosta](architecture.md) · [Decyzje dla DBVictory](decisions.md)

| Rodzina | Lua | OTUI | Inne | Lua na domyślnej ścieżce |
|---|---:|---:|---:|---:|
| Loader główny | 1 | 0 | 0 | 1 |
| Moduły użytkowe vBot | 38 | 15 | 1 | 36 |
| CaveBot | 25 | 3 | 0 | 24 |
| TargetBot | 7 | 3 | 0 | 7 |

## Loader

| Plik źródłowy | W domyślnym starcie | Odpowiedzialność i znaczenie |
|---|---|---|
| [_Loader.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/_Loader.lua) | Ładowany | Ładuje style i 36 modułów w ustalonej kolejności. Biblioteki oraz profil muszą być dostępne przed modułami funkcjonalnymi. |

## Automatyki i interfejs

| Plik źródłowy | W domyślnym starcie | Odpowiedzialność i znaczenie |
|---|---|---|
| [vBot/main.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/main.lua) | Ładowany | Informuje o dostępnej wersji i źródle projektu. Nie dowodzi dostępności instalacji w innym kliencie. |
| [vBot/items.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/items.lua) | Ładowany | Statyczne tabele przedmiotów, cen i materiałów zużywalnych. W DBVictory dane muszą pochodzić z katalogu tej gry. |
| [vBot/vlib.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/vlib.lua) | Ładowany | Wspólne zapytania o świat, istoty i przedmioty, logika cooldownów i rozpoznawanie stanów. Część odpowiedzi jest wnioskowana z tekstu. |
| [vBot/new_cavebot_lib.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/new_cavebot_lib.lua) | Ładowany | Wspólne operacje dla rozszerzeń trasy, m.in. dojście, kontenery, rozmowa, depozyt, bank i podróż. |
| [vBot/configs.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/configs.lua) | Ładowany | Rozdziela ustawienia modułów i warianty profili. Dokładnie testować, który profil zapisuje każdy przełącznik. |
| [vBot/extras.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/extras.lua) | Ładowany | Dodatki do pól i przedmiotów, zaznaczenia, drzwi, skinning i opcje UI. Zbiór niezależnych funkcji o różnych wymaganiach. |
| [vBot/cavebot.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/cavebot.lua) | Ładowany | Drugi loader instaluje sterownik trasy, TargetBot i rozszerzenia. Inwentarz poniżej rozróżnia rejestrację od wykonania akcji. |
| [vBot/playerlist.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/playerlist.lua) | Ładowany | Listy przyjaciół i przeciwników, oznaczanie oraz odświeżenie po zmianie piętra. Klasyfikacja podmiotu i jego wygląd to osobne informacje. |
| [vBot/BotServer.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/BotServer.lua) | Ładowany | Opcjonalna komunikacja grupowa, wymiana wybranych stanów i danych między profilami. Poza potrzebami samodzielnej sesji DBVictory. |
| [vBot/alarms.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/alarms.lua) | Ładowany | Alarmy z tekstu, graczy i zmian stanu, wspólne ograniczenie tempa dźwięku i błysku. W tej wersji warunek alarmu many omyłkowo czyta HP. |
| [vBot/Conditions.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/Conditions.lua) | Ładowany | Reguły reagujące na stany postaci, leczenie statusów i odnawianie efektów. Przy Speed Up preferować rzeczywisty koniec efektu. |
| [vBot/Equipper.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/Equipper.lua) | Ładowany | Zestawy sprzętu i reguły wyboru według warunków oraz kolejności. Weryfikować zmianę odpowiedniego slotu. |
| [vBot/pushmax.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/pushmax.lua) | Ładowany | Moduł operacji na zaznaczonych polach i interakcji PvP. Nie stanowi części mechanizmu nawigacji PvE. |
| [vBot/combo.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/combo.lua) | Ładowany | Reguły koordynacji ataków grupy i lidera. Nie jest wymagany do lokalnego wyboru czaru w DBVictory. |
| [vBot/HealBot.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/HealBot.lua) | Ładowany | Reguły leczenia gracza, przedmioty i czary. Opcja oczekiwania na loot oraz zapis profilu wymagają uwagi opisanej w decyzjach. |
| [vBot/new_healer.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/new_healer.lua) | Ładowany | Osobna konfiguracja leczenia innych postaci według warunków, celu i priorytetu. |
| [vBot/AttackBot.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/AttackBot.lua) | Ładowany | Reguły czarów i przedmiotów bojowych, kształty obszaru, kierunek, zasięg i grupa cooldownów. Dla DBVictory osobno weryfikować czary celowane. |
| [vBot/ingame_editor.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/ingame_editor.lua) | Ładowany | Edycja własnych skryptów w profilu i odświeżenie. W DBVictory edytowalne reguły warto walidować jeszcze przed aktywacją. |
| [vBot/Dropper.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/Dropper.lua) | Ładowany | Reguły upuszczania lub używania wybranych przedmiotów z warunkami udźwigu. Przykład akcji wymagającej potwierdzenia rezultatu. |
| [vBot/Containers.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/Containers.lua) | Ładowany | Otwiera, grupuje, sortuje i łączy stosy. Po uporządkowaniu ogranicza pracę, a zmiana pojemnika lub ekwipunku ją wznawia. |
| [vBot/quiver_manager.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/quiver_manager.lua) | Ładowany | Uzupełnianie amunicji i zarządzanie kołczanem. Wzorzec zależności od dostępności pojemnika. |
| [vBot/quiver_label.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/quiver_label.lua) | Ładowany | Licznik amunicji przy slocie wyposażenia. Aktualizacja prezentacji jest oddzielna od decyzji o uzupełnieniu. |
| [vBot/tools.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/tools.lua) | Ładowany | Drobne narzędzia dla monet i kanału handlowego. Mały zakres i zależności specyficzne dla serwera. |
| [vBot/antiRs.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/antiRs.lua) | Ładowany | Ochrona PvP. Skrypt ma efekt uboczny przy ładowaniu. To dobry przypadek testowy dla zasady zerowych akcji po uruchomieniu samych modułów. |
| [vBot/depot_withdraw.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/depot_withdraw.lua) | Ładowany | Narzędzie wybierające konkretne pojemniki i przedmioty z depozytu. Nazwy i ID są elementem konfiguracji tej gry. |
| [vBot/eat_food.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/eat_food.lua) | Ładowany | Rozpoznanie dostępnej żywności i cykliczne użycie. |
| [vBot/equip.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/equip.lua) | Ładowany | Prostsza obsługa zakładania wyposażenia, obok pełnego modułu Equipper. Warto wyznaczyć jednego właściciela operacji na slocie. |
| [vBot/exeta.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/exeta.lua) | Ładowany | Prowokacja potworów zależna od profesji, odległości, stanu walki i cooldownu grupowego. |
| [vBot/analyzer.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/analyzer.lua) | Ładowany | Sesje, XP, zabicia, loot, zużycie, zysk, statystyki grupowe i rozliczanie przebiegu. Wynik zależy od jakości wejściowych liczników i tekstów. |
| [vBot/spy_level.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/spy_level.lua) | Ładowany | Ręczny przegląd pięter i przywrócenie widoku przy ruchu. Dotyczy kamery i interfejsu. |
| [vBot/supplies.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/supplies.lua) | Ładowany | Progi zaopatrzenia, pojemność i warunki uzupełnienia, migracje przechowywanych ustawień. |
| [vBot/depositer_config.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/depositer_config.lua) | Ładowany | Konfiguracja pojemników, wyjątków i zasad odkładania przedmiotów dla akcji depozytowej. |
| [vBot/npc_talk.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/npc_talk.lua) | Ładowany | Skrót do dialogu z NPC. Brak zastosowania w aktualnym zadaniu połączenia z grą. |
| [vBot/xeno_menu.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/xeno_menu.lua) | Ładowany | Dostosowuje menu kontekstowe i obsługę kliknięć w UI. W Atlasie lepiej ograniczyć zmiany do własnego widoku. |
| [vBot/hold_target.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/hold_target.lua) | Ładowany | Zapamiętuje ostatni cel i sprawdza piętro, umożliwia czyszczenie wyboru. W DBVictory potrzebna również tożsamość sesji. |
| [vBot/cavebot_control_panel.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/cavebot_control_panel.lua) | Ładowany | Kontrolki przerwania, powrotu i uzupełnień przy kolejnym punkcie kontrolnym. Rozdzielić intencję użytkownika od wykonania na trasie. |
| [vBot/Sio.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/Sio.lua) | Opcjonalny | Alternatywna implementacja leczenia innych postaci, poza domyślną listą startową. |
| [vBot/cast_food.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/cast_food.lua) | Opcjonalny | Opcjonalny skrypt czaru związanego z żywnością, poza listą loaderów. |

## Trasa i akcje waypointów

| Plik źródłowy | W domyślnym starcie | Odpowiedzialność i znaczenie |
|---|---|---|
| [cavebot/cavebot.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/cavebot.lua) | Ładowany | Wykonawca kolejnych akcji, bieżący indeks, opóźnienia, próby oraz powrót do osiągalnej części trasy. |
| [cavebot/actions.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/actions.lua) | Ładowany | Rejestr i implementacje poleceń trasy, w tym goto, użycie przedmiotu i użycie na polu. Część poleceń kończy się po zleceniu użycia. |
| [cavebot/config.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/config.lua) | Ładowany | Parametry ruchu i ustawienia CaveBota. |
| [cavebot/editor.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/editor.lua) | Ładowany | Edycja listy oraz przypisanie parametrów zarejestrowanym akcjom. |
| [cavebot/example_functions.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/example_functions.lua) | Ładowany | Przykładowe funkcje dla edytora. Załadowanie zestawu przykładów nie wykonuje zawartych w nim tras. |
| [cavebot/recorder.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/recorder.lua) | Ładowany | Rejestracja pozycji i użyć, progi rozstawu waypointów i osobne traktowanie zmian piętra. |
| [cavebot/walking.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/walking.lua) | Ładowany | Kolejka planowanych kierunków, czas kroku, mapclick, uzgadnianie zmian pozycji. |
| [cavebot/minimap.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/minimap.lua) | Ładowany | Obsługa kliknięć na minimapie, ruch do punktu i menu kontekstowe. Nie jest odpowiednikiem wielopiętrowego planera Atlasu. |
| [cavebot/bank.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/bank.lua) | Ładowany | Akcja bankowa przez dialog, z próbami i harmonogramem. |
| [cavebot/buy_supplies.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/buy_supplies.lua) | Ładowany | Zakupy u NPC według konfiguracji zaopatrzenia. |
| [cavebot/clear_tile.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/clear_tile.lua) | Ładowany | Przygotowanie wskazanego pola, opcje dla drzwi lub zajęcia miejsca. Czynność może zależeć od aktualnego stosu. |
| [cavebot/d_withdraw.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/d_withdraw.lua) | Ładowany | Akcja pobrania z depozytu według parametrów. Oddziela cel zaopatrzenia od pojedynczego kliknięcia. |
| [cavebot/depositor.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/depositor.lua) | Ładowany | Odkładanie przedmiotów według konfiguracji oraz dostępności kontenerów. |
| [cavebot/doors.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/doors.lua) | Ładowany | Obsługa drzwi na trasie. W DBVictory oczekiwać na potwierdzenie stanu pola. |
| [cavebot/imbuing.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/imbuing.lua) | Ładowany | Krok trasy związany z imbuingiem. Samo okno i późniejsze przezbrojenie wymagają odrębnej obsługi. |
| [cavebot/inbox_withdraw.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/inbox_withdraw.lua) | Ładowany | Pobieranie przedmiotów z inboxa jako krok trasy. |
| [cavebot/lure.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/lure.lua) | Ładowany | Akcja trasy zmieniająca warunki lurowania. Współpracuje z TargetBotem. |
| [cavebot/pos_check.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/pos_check.lua) | Ładowany | Kontrola odległości od zadanej pozycji i rozgałęzienie do etykiety w trasie. |
| [cavebot/sell_all.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/sell_all.lua) | Ładowany | Sprzedaż przedmiotów jako krok trasy z interakcją NPC. |
| [cavebot/stand_lure.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/stand_lure.lua) | Ładowany | Postój w celu zebrania grupy jako jawny etap trasy. Podczas porównania rozróżnić go od wyboru pozycji podczas normalnej walki. |
| [cavebot/supply_check.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/supply_check.lua) | Ładowany | Warunkowy wybór dalszego przebiegu na podstawie zapasów. |
| [cavebot/tasker.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/tasker.lua) | Ładowany | Automatyzacja etapów zadań i dialogów NPC. Reguły są zależne od serwera. |
| [cavebot/travel.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/travel.lua) | Ładowany | Podróż z użyciem dialogu oraz sprawdzenia aktualnej pozycji. |
| [cavebot/withdraw.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/withdraw.lua) | Ładowany | Pobranie określonej liczby wybranych przedmiotów z podanego źródła. |
| [cavebot/extension_template.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/extension_template.lua) | Opcjonalny | Szablon autora do rejestracji nowej akcji, poza domyślnym loaderem. |

## Cel, walka i loot

| Plik źródłowy | W domyślnym starcie | Odpowiedzialność i znaczenie |
|---|---|---|
| [targetbot/target.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/targetbot/target.lua) | Ładowany | Główna pętla wyboru celu, koordynacja lootu, ruchu i statusu aktywności. |
| [targetbot/creature.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/targetbot/creature.lua) | Ładowany | Dopasowuje reguły do potwora, zapamiętuje wyniki dla nazw i zarządza konfiguracją. |
| [targetbot/creature_attack.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/targetbot/creature_attack.lua) | Ładowany | Atak, utrzymanie odległości, repozycjonowanie oraz warunkowe przekazanie ruchu do CaveBota. |
| [targetbot/creature_editor.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/targetbot/creature_editor.lua) | Ładowany | Edytor reguł dla nazw i profili stworzeń. |
| [targetbot/creature_priority.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/targetbot/creature_priority.lua) | Ładowany | Wynik priorytetu m.in. według konfiguracji, odległości, życia i obecnego celu. |
| [targetbot/looting.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/targetbot/looting.lua) | Ładowany | Kolejka potencjalnych ciał, dojście, otwarcie kontenerów, obsługa przedmiotów i odmowy własności. |
| [targetbot/walking.lua](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/targetbot/walking.lua) | Ładowany | Wyznacza cel ruchu TargetBota i prowadzi do niego, gdy kontrola ruchu należy do walki. |

## Układy interfejsu i metadane

Pliki OTUI zawierają deklaracje układu i stylów. Działanie przycisku wynika z kodu Lua przypisanego do widżetu. Obecność stylu starszego healera sama nie aktywuje jego skryptu.

- **vBot** — [AttackBot.otui](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/AttackBot.otui), [BotServer.otui](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/BotServer.otui), [Conditions.otui](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/Conditions.otui), [HealBot.otui](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/HealBot.otui), [alarms.otui](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/alarms.otui), [analyzer.otui](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/analyzer.otui), [combo.otui](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/combo.otui), [depositer_config.otui](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/depositer_config.otui), [equipper.otui](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/equipper.otui), [extras.otui](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/extras.otui), [new_healer.otui](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/new_healer.otui), [playerlist.otui](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/playerlist.otui), [pushmax.otui](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/pushmax.otui), [siolist.otui](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/siolist.otui), [supplies.otui](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/supplies.otui).
- **cavebot** — [cavebot.otui](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/cavebot.otui), [config.otui](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/config.otui), [editor.otui](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/cavebot/editor.otui).
- **targetbot** — [creature_editor.otui](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/targetbot/creature_editor.otui), [looting.otui](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/targetbot/looting.otui), [target.otui](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/targetbot/target.otui).
- [vBot/version.txt](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/version.txt) zawiera identyfikator wydania profilu.

Pełny rejestr biblioteki i rozszerzeń wynika z dwóch loaderów. Powiązania między grą, stanem, harmonogramem i interfejsem użytkownika opisuje osobny dokument o architekturze. [Loader główny](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/_Loader.lua#L1-L56) · [Loader CaveBota i TargetBota](https://github.com/Vithrax/vBot/blob/09d6816c817f881735ecf78e87e052fcf7c02816/vBot/cavebot.lua#L1-L53)
