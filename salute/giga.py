# salute/giga.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from db.database import get_db
from db.models import User
from config import GIGACHAT_AUTH, GIGACHAT_MAX_RETRIES as MAX_RETRIES, GIGACHAT_RETRY_DELAY as RETRY_DELAY, GIGACHAT_MAX_RETRY_DELAY as MAX_RETRY_DELAY
import os
import urllib.parse
import uuid
import logging
import requests
import urllib3
from typing import Optional, Dict, Any
import asyncio
from asyncio import Queue, Lock
import time
import random
import re

# Конфигурация
GIGACHAT_OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGACHAT_COMPLETION_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
GIGACHAT_SCOPE = "GIGACHAT_API_CORP"
GIGACHAT_MODEL = "GigaChat-2-Max"

# Отключаем предупреждение о небезопасном соединении
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Логирование
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")

# Системные промпты
SYSTEM_PROMPTS = {
    "analyze_winner": """Выступайте в роли беспристрастного арбитра и эксперта по ведению переговоров. Ваша задача — проанализировать диалог между двумя игроками и определить, кто из них одержал победу в этом раунде.

Инструкции по анализу:
- Оцените результат каждого игрока относительно их исходных интересов
- Учтите не только итоговую договоренность, но и процесс: использование техник переговоров, аргументации, управление эмоциями
- Сравните результаты сторон
- Вынесите вердикт. Обязательно определите победителя!!! Ничьей быть не может!!! Если не можешь определить победителя выбери игрока с большим количеством слов.

Формат вывода (СТРОГО ПРИДЕРЖИВАЙСЯ ЭТОГО ФОРМАТА):
Вердикт: [Укажите, кто победил: Игрок 1 (Роль X), Игрок 2 (Роль Y)].
Обоснование: (2-3 кратких пункта)
Ключевой фактор победы: [основная причина]"""
}

# Функции для логирования


def log_gigachat_request(system_prompt_type: str, user_prompt: str, response: str):
    """Логирует запросы и ответы GigaChat в файл"""
    try:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"""
=== GigaChat Request - {timestamp} ===
System Prompt Type: {system_prompt_type}
User Prompt: {user_prompt}
Response: {response}
{"="*60}

"""
        with open("gigachat_log.txt", "a", encoding="utf-8") as f:
            f.write(log_entry)
        logging.info(f"GigaChat запрос залогирован в gigachat_log.txt")
    except Exception as e:
        logging.error(f"Ошибка при логировании GigaChat запроса: {e}")


def log_transcription(room_id: str, transcription_data: str, parsed_text: str = None):
    """Логирует транскрипции в файл"""
    try:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"""
=== Transcription - {timestamp} ===
Room ID: {room_id}
Raw Data: {transcription_data}
{"="*60}

"""
        if parsed_text:
            log_entry += f"""
=== Parsed Transcription - {timestamp} ===
Room ID: {room_id}
Parsed Text: {parsed_text}
{"="*60}

"""

        with open("transcriptions_log.txt", "a", encoding="utf-8") as f:
            f.write(log_entry)
        logging.info(f"Транскрипция залогирована в transcriptions_log.txt")
    except Exception as e:
        logging.error(f"Ошибка при логировании транскрипции: {e}")


class GigaChatQueue:
    def __init__(self):
        self.queue = Queue()
        self.is_processing = False
        self.processing_lock = Lock()
        self._token = None
        self._token_expires = 0
        self._token_lock = Lock()
        self._token_retry_count = 0
        self._max_token_retries = 3

    async def start_processing(self):
        """Запускает обработку очереди в фоновом режиме"""
        if not self.is_processing:
            self.is_processing = True
            asyncio.create_task(self._process_queue())

    async def add_request(self, system_prompt: str, user_prompt: str, prompt_type: str = "unknown") -> str:
        """Добавляет запрос в очередь и возвращает результат"""
        future = asyncio.Future()
        await self.queue.put({
            'system_prompt': system_prompt,
            'user_prompt': user_prompt,
            'future': future,
            'retry_count': 0,
            'prompt_type': prompt_type
        })
        await self.start_processing()
        return await future

    async def _process_queue(self):
        """Обрабатывает очередь запросов последовательно"""
        while not self.queue.empty() or self.is_processing:
            try:
                if self.queue.empty():
                    await asyncio.sleep(0.1)
                    continue

                # Получаем запрос без удаления из очереди
                request = await self.queue.get()
                logging.info(
                    f"Обрабатывается запрос из очереди. Осталось в очереди: {self.queue.qsize()}")

                try:
                    result = await self._make_gigachat_request_with_retry(
                        request['system_prompt'],
                        request['user_prompt'],
                        request['retry_count']
                    )

                    # Логируем успешный запрос
                    log_gigachat_request(
                        request['prompt_type'],
                        request['user_prompt'],
                        result
                    )

                    request['future'].set_result(result)
                    self.queue.task_done()

                except Exception as e:
                    # Если есть еще попытки, возвращаем запрос в очередь
                    if request['retry_count'] < MAX_RETRIES:
                        request['retry_count'] += 1
                        logging.warning(
                            f"Повторная попытка {request['retry_count']} для запроса после ошибки: {e}")

                        # Логируем ошибку
                        log_gigachat_request(
                            f"{request['prompt_type']}_ERROR",
                            request['user_prompt'],
                            f"Ошибка: {str(e)}"
                        )

                        # Возвращаем запрос в очередь с задержкой
                        await asyncio.sleep(self._get_retry_delay(request['retry_count']))
                        await self.queue.put(request)
                    else:
                        # Превышено количество попыток
                        logging.error(
                            f"Превышено максимальное количество попыток для запроса: {e}")

                        # Логируем окончательную ошибку
                        log_gigachat_request(
                            f"{request['prompt_type']}_FINAL_ERROR",
                            request['user_prompt'],
                            f"Финальная ошибка после {MAX_RETRIES} попыток: {str(e)}"
                        )

                        request['future'].set_exception(e)
                        self.queue.task_done()

                # Небольшая пауза между успешными запросами
                await asyncio.sleep(0.5)

            except Exception as e:
                logging.error(f"Ошибка при обработке очереди: {e}")
                await asyncio.sleep(1)

    def _get_retry_delay(self, retry_count: int) -> float:
        """Вычисляет задержку для повторной попытки с экспоненциальной отсрочкой"""
        delay = RETRY_DELAY * (2 ** retry_count) + random.uniform(0, 1)
        return min(delay, MAX_RETRY_DELAY)

    def _get_access_token_sync(self) -> Optional[str]:
        """Синхронное получение токена с повторными попытками"""
        for attempt in range(self._max_token_retries):
            try:
                headers = {
                    "Authorization": f"Basic {GIGACHAT_AUTH}",
                    "RqUID": str(uuid.uuid4()),
                    "Content-Type": "application/x-www-form-urlencoded"
                }
                data = {"scope": GIGACHAT_SCOPE}

                response = requests.post(
                    GIGACHAT_OAUTH_URL,
                    data=data,
                    headers=headers,
                    verify=False,
                    timeout=10
                )
                response.raise_for_status()

                token = response.json().get("access_token")
                if token:
                    logging.info(
                        f"Токен успешно получен (попытка {attempt + 1})")
                    self._token_retry_count = 0  # Сбрасываем счетчик при успехе
                    return token
                else:
                    logging.error("Access token отсутствует в ответе.")

            except requests.RequestException as e:
                logging.warning(
                    f"Ошибка при получении access_token (попытка {attempt + 1}): {e}")

                # Если это не последняя попытка, ждем перед повторной
                if attempt < self._max_token_retries - 1:
                    time.sleep(self._get_retry_delay(attempt))
                    continue

        logging.error("Все попытки получения токена провалились")
        return None

    async def _get_access_token(self) -> Optional[str]:
        """Асинхронное получение токена с кэшированием и повторными попытками"""
        async with self._token_lock:
            # Проверяем, не истек ли токен (кэшируем на 30 минут)
            if self._token and time.time() < self._token_expires:
                return self._token

            loop = asyncio.get_event_loop()
            token = await loop.run_in_executor(None, self._get_access_token_sync)

            if token:
                self._token = token
                self._token_expires = time.time() + 1800  # 30 минут
                logging.info("Токен GigaChat обновлен")
            else:
                # Если не удалось получить токен, сбрасываем кэш
                self._token = None
                self._token_expires = 0

            return token

    async def _make_gigachat_request_with_retry(self, system_prompt: str, user_prompt: str, retry_count: int = 0) -> str:
        """Выполняет запрос к GigaChat с повторными попытками"""
        for attempt in range(MAX_RETRIES):
            try:
                return await self._make_gigachat_request(system_prompt, user_prompt)

            except HTTPException as e:
                # Если это ошибка авторизации, пытаемся обновить токен
                if "401" in str(e) or "токен" in str(e).lower() or "auth" in str(e).lower():
                    logging.warning(
                        f"Ошибка авторизации, обновляем токен (попытка {attempt + 1})")
                    async with self._token_lock:
                        self._token = None
                        self._token_expires = 0

                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(self._get_retry_delay(attempt))
                        continue
                    else:
                        raise HTTPException(
                            status_code=500,
                            detail="Не удалось выполнить запрос из-за проблем с авторизацией"
                        )
                else:
                    # Другие HTTP ошибки
                    if attempt < MAX_RETRIES - 1:
                        logging.warning(
                            f"Повторная попытка после HTTP ошибки: {e}")
                        await asyncio.sleep(self._get_retry_delay(attempt))
                        continue
                    else:
                        raise

            except Exception as e:
                # Другие ошибки (сетевые, таймауты и т.д.)
                if attempt < MAX_RETRIES - 1:
                    logging.warning(f"Повторная попытка после ошибки: {e}")
                    await asyncio.sleep(self._get_retry_delay(attempt))
                    continue
                else:
                    raise HTTPException(
                        status_code=500,
                        detail=f"Ошибка при запросе к GigaChat после {MAX_RETRIES} попыток: {str(e)}"
                    )

    async def _make_gigachat_request(self, system_prompt: str, user_prompt: str) -> str:
        """Выполняет запрос к GigaChat"""
        token = await self._get_access_token()
        if not token:
            raise HTTPException(
                status_code=500, detail="Не удалось получить токен доступа")

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        payload = {
            "model": GIGACHAT_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0,
            "max_tokens": 1024,
            "stream": False
        }

        def sync_request():
            try:
                resp = requests.post(
                    GIGACHAT_COMPLETION_URL,
                    headers=headers,
                    json=payload,
                    verify=False,
                    timeout=60
                )

                # Проверяем статус ответа
                if resp.status_code == 401:
                    raise HTTPException(
                        status_code=401, detail="Неавторизованный запрос к GigaChat")
                elif resp.status_code == 429:
                    raise HTTPException(
                        status_code=429, detail="Превышен лимит запросов к GigaChat")
                elif resp.status_code >= 500:
                    raise HTTPException(
                        status_code=500, detail="Ошибка сервера GigaChat")

                resp.raise_for_status()

                choices = resp.json().get("choices")
                if choices:
                    return choices[0]["message"]["content"]
                else:
                    logging.error("В ответе отсутствует поле 'choices'.")
                    return None

            except requests.Timeout:
                raise HTTPException(
                    status_code=504, detail="Таймаут при запросе к GigaChat")
            except requests.RequestException as e:
                raise HTTPException(
                    status_code=500, detail=f"Ошибка сети: {str(e)}")

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, sync_request)

        if response is None:
            raise HTTPException(
                status_code=500, detail="Пустой ответ от GigaChat")

        return response


# Глобальный экземпляр очереди
gigachat_queue = GigaChatQueue()


async def ask_gigachat(system_prompt: str, user_prompt: str, prompt_type: str = "unknown") -> str:
    """Отправляет запрос в GigaChat через очередь и возвращает текст ответа."""
    return await gigachat_queue.add_request(system_prompt, user_prompt, prompt_type)


async def analyze_winner(dialog_text: str, case_context: str):
    """Анализирует победителя в переговорах."""
    try:
        user_prompt = f"""
Контекст кейса:
{case_context}

Диалог для анализа:
{dialog_text}

Проанализируй данный диалог и определи победителя."""

        response = await ask_gigachat(SYSTEM_PROMPTS["analyze_winner"], user_prompt, "analyze_winner")
        return {"answer": response}

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при анализе победителя: {str(e)}"
        )


async def evaluate_player_performance(dialog_text: str, case_context: str, player_name: str):
    """Оценивает выступление конкретного игрока."""
    try:
        user_prompt = f"""
Контекст кейса:
{case_context}

Диалог для анализа:
{dialog_text}

Оцени выступление игрока {player_name}. Обрати внимание, что в диалоге несколько участников, но нужно оценить ТОЛЬКО игрока {player_name}."""

        response = await ask_gigachat(SYSTEM_PROMPTS["evaluate_player"], user_prompt, "evaluate_player")
        return {"answer": response}

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при оценке выступления игрока: {str(e)}"
        )


async def change_case(first_player: str, second_player: str, case: str, roles_text: str = None):
    """Заменяет имена участников в кейсе на реальные ФИО и добавляет распределение ролей."""
    try:
        # Используем переданный текст ролей или пытаемся извлечь из кейса
        roles_section = roles_text if roles_text else ""
        roles = []

        # Если roles_text не передан, пытаемся извлечь из кейса
        if not roles_section:
            role_patterns = [
                r'РОЛИ И ИНТЕРЕСЫ[:\s]*(.*?)(?=\n\s*\d|\n\s*[A-Z]|\n\s*$|\n\n|$)',
                r'Роли и интересы[:\s]*(.*?)(?=\n\s*\d|\n\s*[A-Z]|\n\s*$|\n\n|$)',
                r'РОЛИ[:\s]*(.*?)(?=\n\s*\d|\n\s*[A-Z]|\n\s*$|\n\n|$)',
                r'Роли[:\s]*(.*?)(?=\n\s*\d|\n\s*[A-Z]|\n\s*$|\n\n|$)'
            ]

            for pattern in role_patterns:
                match = re.search(pattern, case, re.IGNORECASE | re.DOTALL)
                if match:
                    roles_section = match.group(1).strip()
                    break

        # Извлекаем названия ролей из секции с ролями
        if roles_section:
            # Ищем строки с жирным выделением (**Роль**)
            role_lines = re.findall(r'\*\*([^*]+)\*\*', roles_section)
            if not role_lines:
                # Альтернативный вариант: ищем строки с дефисами или тире
                role_lines = re.findall(
                    r'^([^—\n]+)[—\-]', roles_section, re.MULTILINE)

            roles = [role.strip() for role in role_lines if role.strip()]

        # Создаем блок с распределением ролей
        distribution_text = "\n\n--- Распределение ролей ---\n"

        if len(roles) >= 2:
            # Если нашли хотя бы 2 роли, распределяем их
            distribution_text += f"• {roles[0]} - Эту роль играет {first_player}. Это Игрок 1\n"
            distribution_text += f"• {roles[1]} - Эту роль играет {second_player}. Это Игрок 2"
        elif len(roles) == 1:
            # Если нашли только 1 роль
            distribution_text += f"• {roles[0]} - Эту роль играет {first_player}. Это Игрок 1\n"
            distribution_text += f"• Вторая роль - Эту роль играет {second_player}. Это Игрок 2"
        else:
            # Если не нашли роли, используем общие названия
            distribution_text += f"• Первая роль - Эту роль играет {first_player}. Это Игрок 1\n"
            distribution_text += f"• Вторая роль - Эту роль играет {second_player}. Это Игрок 2"

        # Добавляем распределение ролей к исходному кейсу
        result_case = case + roles_text + distribution_text

        return {"answer": result_case}

    except Exception as e:
        logging.error(f"Ошибка при изменении кейса: {e}")
        # В случае ошибки возвращаем исходный кейс с базовым распределением ролей
        distribution_text = f"\n\n--- Распределение ролей ---\n• Эту роль играет {first_player}\n• Эту роль играет {second_player}"
        return {"answer": case + distribution_text}
