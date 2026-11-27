# Przegląd walki i kierunek rozwoju V2

Stan: 2026-11-16. To propozycje dalszych zmian, poza regułami opisanymi jako wdrożone.
Opisane niżej `actions/cavebot.py`, `actions/cavebot2.py` i `actions/auto_targeting2.py`
zostały później usunięte; ich obowiązki przejął `adaptive_cavebot`.
Weryfikacja obecnych zmian: testy syntetyczne; nowe zachowania walki nie były
uruchamiane na postaci.

## Co zostaje z obecnych implementacji

`actions/cavebot.py`: odtwarzanie nagrania, ponowne odnalezienie fragmentu trasy,
wykrywanie braku obrażeń i statystyki odcinków (czas, XP, zabójstwa, zużycie Senzu).
Przydatne są statystyki i odzyskiwanie trasy. Nie kopiujemy ruchu przez używanie
przypadkowego ID podłoża ani pomijania obowiązkowych przejść.

`actions/cavebot2.py`: liczba lurowanych mobów zależna od czasu walki i pozostałego
Ki, próba odsunięcia się od blokującego moba, wskazanie blokera do zabicia,
omijanie pustych odcinków i czekanie w miejscach częstego respawnu. Warto przenieść
obsługę blokady i pomiar walki. Samoczynne zwiększanie grupy lub zmiana trasy
powinny wymagać osobnego ustawienia i ograniczeń użytkownika.

`actions/auto_targeting2.py`: wybór najwyższego HP i wymuszenie celu przy blokadzie.
Nie przenosimy dopuszczania starych obserwacji (60 s), braku pozycji ani
rozpoznawania mobów wyłącznie po zakresie ID. V2 zachowuje świeże dane DLL+mapy.

`actions/auto_combat.py`: stałe czary/AoE i blokowanie czarów podczas lure.
V2 używa jawnej konfiguracji i atakuje podczas lure, zgodnie z wymaganiem użytkownika.

## Wdrożone w tej zmianie

- Domyślna akcja reguły: „Zaznacz i atakuj”.
- Nadpisanie ruchu per potwór: wspólne ustawienie / stój / goń / dystans / lure.
- Nadpisanie czarów: wspólne / bez czarów / wybrany czar z CD, Ki i zasięgiem.
- Zmiana celu nie resetuje cooldownu; priorytet i `*` pozostają obsługiwane.
- Szczegóły rozwijane jedną ikoną; konfiguracja nie uruchamia automatyki.

## Zalecana kolejność kolejnych usprawnień

1. **Cel i dostępność:** nie zmieniaj celu przy remisie, dopóki żyje i jest
   osiągalny; przełącz natychmiast na wyższy priorytet. Osobno oceniaj drogę
   dojścia i linię strzału. Wymaga potwierdzenia flag blokujących pociski.
2. **Blokada drogi:** spróbuj widocznego obejścia, następnie potraktuj pasującego
   do listy ataku blokera jako pilny cel; po ograniczonej liczbie prób pauza
   z powodem. Nigdy nie obchodź wykluczenia „Ignoruj”.
3. **Lure jako grupa:** limit liczby (np. 4), odstęp, czas oraz zakaz wciągania
   grupy w schody/drzwi/ślepy korytarz. Po zebraniu grupy walka do jej usunięcia.
   Adaptacyjny limit dopiero po zebraniu statystyk i z jawnym maksimum.
4. **Czary warunkowe:** pojedynczy cel / grupa, minimalne Ki, zasięg i brak
   postronnych graczy w obszarze. Dla czarów kierunkowych potrzebna geometria
   obszaru i obrót postaci; nie zgadujemy ich po nazwie czaru.
5. **Krótka historia decyzji:** cel, dopasowana reguła, dystans, pozostały CD,
   powód postoju. Statystyki pętli pomagają ocenić zmianę konfiguracji bez
   dokładania kontrolek do głównego widoku.

## Źródła inspiracji

Wyszukano materiały o ElfBocie, lecz nie potwierdzono zachowanej dokumentacji
wydawcy. Nie traktujemy poradników społeczności jako specyfikacji protokołu.
Technicznie porównano publiczny kod projektu OTCv8:

- [Edytor potwora vBot](https://github.com/OTCv8/otclientv8/blob/master/modules/game_bot/default_configs/vBot_4.8/targetbot/creature_editor.lua):
  rozdzielone priorytet i zagrożenie, dystans, zakotwiczenie oraz warianty lure.
- [Walka i ruch TargetBot](https://github.com/OTCv8/otclientv8/blob/master/modules/game_bot/default_configs/cavebot_1.3/targetbot/creature_attack.lua):
  ataki specyficzne dla celu, warunki grupy, odstęp N–N+1 i decyzje o ruchu.

To inspiracja do własnego projektu; kod klienta OTCv8 nie potwierdza zgodności
z serwerem DBVictory ani niewykrywalności bota.
