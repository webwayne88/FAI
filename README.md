# Battle Bot 4 Pro Max – System Overview

## 1. Что это за проект
Асинхронный игровой бот для Telegram с веб‑админкой. Система автоматически:

- принимает регистрацию игроков, хранит их профили и предпочтения;
- создает «комнаты» Salute Jazz и тайм‑слоты турниров;
- рассылает подтверждения и персонализированные кейсы;
- следит за посещаемостью, фиксирует отказников;
- получает транскрипты, определяет победителя и уведомляет участников;
- предоставляет административный API/UI для мониторинга и ручных запусков.

## 2. Стек

| Слой | Технологии |
| --- | --- |
| Бот | `aiogram`, Telegram Bot API |
| Веб/админ | `FastAPI`, `pydantic`, `SQLAlchemy` |
| Планирование | AIO + собственные сервисы (`app/core/*`) |
| БД | PostgreSQL (`asyncpg`, `SQLAlchemy ORM`) |
| Внешние API | Salute Jazz (создание комнат, транскрипции), GigaChat (генерация кейсов/аналитики) |

## 3. Директории

- `app/` – конфигурация и доменные сервисы:
  - `config/settings.py` – загрузка `.env`, настройки.
  - `container.py` – DI контейнер, создающий `Bot`, сервисы, Jazz клиент.
  - `core/attendance` – `AttendanceGuard` следит за присутствием.
  - `core/messaging` – рассылка сообщений (кейсы, итоги, нотификации).
  - `core/matchmaking` – обработка результатов, подсчет побед, трансляция.
  - `core/scheduling` – MatchScheduler, CaseDispatchService.
- `bot/` – Telegram‑бот: хендлеры (`handlers`), планировщик, утилиты.
- `admin/` – FastAPI роуты и UI (HTML + JS) для управления турниром.
- `common/` – вспомогательные функции (например, работа с часовыми поясами).
- `db/` – модели SQLAlchemy, соединение (`database.py`).
- `salute/` – клиент Salute Jazz и утилиты разбора транскрипций.
- `tests/` – модульные/интеграционные тесты для core‑сервисов.

## 4. Жизненный цикл матча

1. **Регистрация** – игроки взаимодействуют с Telegram‑ботом (`bot/handlers/registration.py`), данные сохраняются в `users`.
2. **Планирование**  
   - Администратор вызывает `POST /api/tournament/schedule` или запускается крон (`main.py -> scheduled_task`).  
   - `MatchScheduler.schedule_matches` выбирает активных игроков, ищет свободные слоты, при необходимости вызывает `create_rooms_and_slots`.
3. **Подтверждение**  
   - После назначения `MatchScheduler` вызывает `send_confirmation_request` (через контейнер).  
   - Пользователи взаимодействуют с inline‑клавиатурой; `bot/handlers/confirm.py` фиксирует подтверждение/отказ.  
   - Оба игрока подтвердили ➜ матч статус `CONFIRMED`, создается персональный кейс, запускается отложенная задача `send_links_and_process` (за 2 минуты до старта отправит ссылку и позже обработает матч).
4. **Перед матчем**  
   - `on_match_confirmed` вызывает `send_personalized_case` за `CASE_READ_TIME` до начала.  
   - За `LINK_FOLLOW_TIME` вызывается `send_link`, чтобы вручную отправить Jazz ссылку (единственная точка рассылки).
5. **Во время матча**  
   - `AttendanceGuard.watch_slot` (инициализируется в контейнере) периодически опрашивает Jazz API. Если кто‑то не пришел до истечения grace‑периода – матч помечается `CANCELED`, игроку увеличивается `declines_count`, отправляются уведомления.
6. **После матча**  
   - `process_match_after_completion` ждет окончание, скачивает транскрипцию, обновляет ссылку комнаты (создает новую Jazz room), сохраняет текст, запускает `process_completed_match`.
   - `MatchResultService.process_slot` анализирует транскрипцию (через GigaChat), определяет победителя, обновляет статистику, элиминирует проигравшего (если `slot.elimination`), формирует сводку.
   - `send_match_results` пересылает итог игрокам.
7. **Фоновые задачи**  
   - `MatchResultService.run_pending_loop` периодически добирает ещё не обработанные слоты (например, если GigaChat был недоступен).
   - `CaseDispatchService.schedule` может использоваться для дополнительных уведомлений (сейчас отключено, см. ниже).

## 5. Ключевые файлы и функции

### app/config/settings.py
Загружает конфигурацию через `pydantic_settings`. Валидация полей (бот токен, Jazz SDK, тайминги). Есть валидаторы для вычисления `slot_duration_minutes` и `case_dispatch_lead_seconds`, если явно не заданы.

### config.py
Фасад над `settings`: экспортирует константы, часовые зоны, списки допустимых слотов (`TOURNAMENT_SLOT_STARTS_MSK`). Используется по всему коду.

### app/container.py
Создает единый `AppContainer`: Telegram Bot, Dispatcher, MessageService, CaseDispatchService, AttendanceGuard, MatchScheduler, MatchResultService. Подготавливает Jazz API клиент, пробрасывает вспомогательные функции (например, `confirmation_sender`).

### app/core/scheduling/service.py (MatchScheduler)
Основные методы:

- `schedule_matches(target_date, elimination, tournament_mode)` – выбирает активных пользователей, проверяет наличие свободных слотов, при отсутствии вызывает `create_rooms_and_slots`, распределяет игроков по слотам, назначает статус `SCHEDULED`, запускает подтверждения, выдачу кейсов и мониторинг посещаемости.
- `_get_available_room_slots` – ищет свободные слоты в нужный день (учитывает `INVITATION_TIMEOUT` для текущего дня).
- `_get_active_users` – фильтрует игроков, которые зарегистрированы, не выбыли и ещё не заняты слотом в этот день.
- `create_rooms_and_slots` – создает недостающие Jazz комнаты и набор слотов согласно `TOURNAMENT_SLOT_STARTS_MSK`.
- `_confirm_players` – отправляет обоим игрокам инвайты через `bot.handlers.confirm.send_confirmation_request`.

### app/core/scheduling/case_dispatcher.py
Планировщик выдачи кейсов:

- `schedule(slot_id)` – отменяет предыдущую задачу и создает новую корутину `_deliver`.
- `_deliver` – ждёт `lead_time_seconds` до начала слота, загружает слот из БД, и вызывает `message_service.send_case_delivery`. Сейчас эта отправка отключена (см. `MessageService`), т.к. ссылка шлется позже из confirm‑handler.

### app/core/matching/service.py (MatchResultService)

- `process_slot(session, slot)` – проверяет наличие транскрипции, вычисляет анализы игроков через GigaChat (`analyzer`), считает длину текста (fallback победителя), обновляет счетчики побед/элиминации, помечает `transcription_processed`.
- `process_pending()` / `run_pending_loop()` – фоновые задачи для «висящих» слотов (например, транскрипция задержалась).
- `send_match_results(slot)` – отправляет итоговое резюме игрокам через MessageService.

### app/core/messaging/service.py
Сейчас глобальная рассылка ссылки отключена (метод `send_case_delivery` пишет в лог и ничего не отправляет). Конкретные сообщения:

- `notify_missing_participants` – сообщить присутствующим, если второго игрока нет.
- `send_match_summary` – итоговое сообщение после анализа.
- `send_custom` / `_broadcast` – универсальная отправка сообщений.
- `_format_slot_time` – форматирует время слота по МСК.

### app/core/attendance/guard.py (AttendanceGuard)

- `watch_slot(slot_id)` – запускает мониторинг одного слота (опрашивает Jazz participants API, пока не наступит начале или deadline).
- `_fetch_snapshot` – извлекает список участников комнаты, сопоставляет с игроками (по нормализованному имени).
- `_mark_present` – при успешном присутствии обновляет статус слота на `CONFIRMED`.
- `_handle_no_show` – при неявке помечает слот как `CANCELED`, увеличивает `declines_count`, зовет `message_service.notify_missing_participants`.

### bot/handlers/confirm.py
Крупнейший модуль — контролирует весь пользовательский флоу в Telegram.

- `send_confirmation_request` – инлайн-кнопки «Приду/Не смогу», автоматическая проверка ответа через `check_confirmation_response`.
- `assign_case_to_slot` – выбирает случайный активный кейс, который не выдавался участникам, логирует в `user_case_history`.
- `handle_cancellation` – отмена матча, элиминация игрока при режиме с выбыванием.
- `on_match_confirmed` – персонализация кейса (через GigaChat `change_case`), запуск двух отложенных задач:
  - `send_case_before_match` – за `CASE_READ_TIME`.
  - `send_links_and_process` – за `LINK_FOLLOW_TIME` (сейчас это единственная отправка Jazz ссылки) и последующая `process_match_after_completion`.
- `process_match_after_completion` – описано выше: скачивает транскрипцию, обновляет комнату, запускает обработку результатов.
- `save_transcription`, `check_player_connection`, `send_link` – вспомогательные функции.

### bot/scheduler.py
Обёртка вокруг `container.match_scheduler` — используется в админском API и в кроне (`main.py`).

### admin/routers/tournament.py
FastAPI роуты:

- `GET /stats` – общая статистика.
- `POST /schedule` – ручной запуск MatchScheduler (с параметром `elimination`).
- `DELETE /slots/{date}` – очистка/удаление слотов.
- `GET /rooms`, `/room/{id}/schedule`, `/upcoming-matches` – мониторинг комнат и расписания.
- `POST /reset-cycle` – сброс статистики.

### main.py
Точка входа Telegram‑бота:

- Инициализирует `dp`, регистрирует хендлеры, команды.
- Запускает APScheduler для `scheduled_task` (каждый день в 20:00).
- Запускает фон `match_result_service.run_pending_loop`.

### db/models.py & db/database.py
Определяют все ORM сущности (Users, Rooms, RoomSlots, Cases, UserCaseHistory), Индексы, связи. `database.py` содержит `async_session` и helper `get_db`.

### tests/
- `tests/messaging/test_message_service.py` – проверка уведомлений.
- `tests/attendance/test_guard.py` – симуляции поведения `AttendanceGuard`.
- Другие тесты (matchmaking/scheduling) акцентированы на core‑логике.

## 6. Как всё связать

1. **Инициализация**: `get_container()` поднимает все сервисы, `main.py` использует его для запуска бота, `admin` – для API.
2. **Scheduler API**: веб‑панель дергает `admin/routers/tournament.py`, который обращается к `bot/scheduler.schedule_matches`.
3. **MatchScheduler**: находит слоты, назначает игроков, любые необходимые комнаты создаёт через Salute Jazz API (`salute/jazz.py`).
4. **Telegram flow**: `send_confirmation_request` → inline реакции → `on_match_confirmed` → `send_personalized_case` → `send_link` → `process_match_after_completion`.
5. **Post-game**: транскрипция → `MatchResultService` → `MessageService.send_match_summary`.
6. **Фоновые наблюдатели**: `AttendanceGuard` следит за посещаемостью, `CaseDispatchService` можно переиспользовать для будущей автоматизации (сейчас отключен, но инфраструктура готова).

## 7. Настройка

1. Скопируйте `.env` (см. `app/config/settings.py` для списка переменных).
2. `pip install -r requirements.txt`.
3. Поднимите PostgreSQL (см. `docker-compose.yml`) и выполните миграции/инициализацию (`db/database.py:init_db`).
4. Для запуска:
   ```bash
   python -m main          # Telegram бот
   uvicorn admin.main:app  # если нужен административный API/панель
   ```
5. Для тестов: `python -m pytest`.

## 8. Полезные советы при разработке

- Вся работа с внешними API (GigaChat, Salute Jazz) вынесена в `salute/` — при тестировании можно подменять эти клиенты.
- Чтобы избежать двойных отправок ссылок, используйте только `send_links_and_process`. `MessageService.send_case_delivery` теперь ничего не рассылает.
- При дебаге побед/выбывания смотрите логи `MatchResultService`: туда добавлены предупреждения о пропущенных слотах и пустых транскрипциях.
- Если меняете расписание слотов, обновите `config.py::TOURNAMENT_SLOT_STARTS_MSK` и/или окружение (`allowed_case_hours_msk`).

README покрывает основной pipeline, но код хорошо разделен по слоям, поэтому новые сценарии (например, Swiss‑турнир, дополнительные уведомления) можно реализовать, добавляя сервисы в `app/container.py` и интегрируя их в соответствующие стадии пайплайна.
