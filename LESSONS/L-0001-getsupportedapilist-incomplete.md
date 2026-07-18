---
id: L-0001
addressee: executor
trigger: [api, steam-api, endpoint-discovery, verification]
target: "вывод «метод/интерфейс недоступен в Steam Web API» сделан только по `GetSupportedAPIList`, без сверки с внешним источником"
status: candidate
evidence:
  hits: 1
  situations:
    - "2026-07-18, разведка партнёрского Web API на wishlist/sales-данные: три захода тем же методом (`ISteamWebAPIUtil/GetSupportedAPIList` на `api.steampowered.com` и `partner.steam-api.com`, включая повтор после включения Financial-скоупа в Steamworks) дали ложный вывод «данных нет по любому ключу»; фактически `IPartnerFinancialsService.GetAppWishlistReporting`/`GetDetailedSales` существуют, документированы на `partner.steamgames.com/doc/webapi` и вернули реальные данные по ключу владельца — но не публикуются в `GetSupportedAPIList` вообще. Владелец троекратно поправил дословно: «доступны доступны, глянь вроде точно есть» и «Я добавил еще галочку Financial. Может быть поэтому тебе не было доступно... Проверь.» (источник: диалог сессии, шаги 2–9; санкция владельца по §3.2 — hits=1 достаточно для входа в карантин)."
  helped: null
  refuted: 0
born: 2026-07-18
last-touched: 2026-07-18
---
Правило: перед выводом «метод/интерфейс X недоступен в Steam Web API» по данным `ISteamWebAPIUtil/GetSupportedAPIList` — кросс-провeрь по независимому источнику (Steamworks partner-документация `partner.steamgames.com/doc/webapi` и/или сторонний индекс методов, собранный из JS-бандлов сайтов Valve, напр. `steamapi.xpaw.me`), прежде чем сообщать вывод как окончательный. Повторная проверка ТЕМ ЖЕ методом (другой домен, другой ключ, добавленный скоуп доступа) не считается кросс-проверкой и не снимает промах.
Почему: механизм промаха — `GetSupportedAPIList` принят за исчерпывающий реестр, хотя это витрина: в этой же разведке он отдал 3 метода из 12 у `IWishlistService`, 1 из 43 у `IPlayerService`, 1 из 20 у `IPublishedFileService`, и вовсе не показал `IPartnerFinancialsService`. Повтор проверки тем же инструментом структурно не мог вскрыть промах — нужен независимый источник, а не более настойчивая проверка того же самого.
