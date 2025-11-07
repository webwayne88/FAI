import aiohttp
import jwt
import base64
import json
import uuid
import asyncio
import re
import os
from datetime import datetime, timedelta
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.backends import default_backend
from dotenv import load_dotenv

load_dotenv()

class SberJazzAPI:
    def __init__(self, sdk_key_base64: str, user_id: str):
        self.sdk_key_base64 = sdk_key_base64
        self.user_id = user_id
        self.transport_token = None
        self.access_token = None
        self.token_expiry = datetime.utcnow()
        self.private_key, self.project_id, self.key_id = self._parse_sdk_key()
        
    def _parse_sdk_key(self):
        """Парсим SDK ключ и извлекаем необходимые данные"""
        try:
            # Декодируем base64
            decoded_bytes = base64.b64decode(self.sdk_key_base64)
            
            # Пытаемся декодировать как JSON
            try:
                sdk_data = json.loads(decoded_bytes.decode('utf-8'))
                
                if 'key' not in sdk_data:
                    raise ValueError("Invalid SDK key format: 'key' field missing")
                    
                jwk = sdk_data['key']
                project_id = sdk_data.get('projectId', '')
                
                # Преобразуем JWK в приватный ключ
                d = base64.urlsafe_b64decode(jwk['d'] + '==')
                x = base64.urlsafe_b64decode(jwk['x'] + '==')
                y = base64.urlsafe_b64decode(jwk['y'] + '==')
                
                private_value = int.from_bytes(d, 'big')
                public_numbers = ec.EllipticCurvePublicNumbers(
                    x=int.from_bytes(x, 'big'),
                    y=int.from_bytes(y, 'big'),
                    curve=ec.SECP384R1()
                )
                
                private_key = ec.EllipticCurvePrivateNumbers(
                    private_value=private_value,
                    public_numbers=public_numbers
                ).private_key(default_backend())
                
                key_id = jwk.get('kid', '')
                
                return private_key, project_id, key_id
                
            except (UnicodeDecodeError, json.JSONDecodeError):
                # Если не JSON, пробуем обработать как PEM ключ
                try:
                    private_key = serialization.load_pem_private_key(
                        decoded_bytes, 
                        password=None, 
                        backend=default_backend()
                    )
                    # Для PEM ключа project_id и key_id нужно получить из других источников
                    return private_key, os.getenv("JAZZ_PROJECT_ID", ""), os.getenv("JAZZ_KEY_ID", "")
                except Exception:
                    raise ValueError("SDK key is neither valid JSON nor PEM format")
                    
        except Exception as e:
            raise ValueError(f"SDK key parsing error: {str(e)}")

    async def _generate_transport_token(self):
        """Генерируем транспортный токен с использованием EC ключа"""
        current_time = datetime.utcnow()
        expiration_time = current_time + timedelta(days=15)
        payload = {
            "iat": int(current_time.timestamp()),
            "exp": int(expiration_time.timestamp()),
            "jti": str(uuid.uuid4()),
            "sub": self.user_id,
            "sdkProjectId": self.project_id
        }
        
        return jwt.encode(
            payload,
            self.private_key,
            algorithm="ES384",
            headers={"kid": self.key_id}
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
        reraise=True
    )
    async def _get_access_token(self, force_refresh=False):
        """Получаем access token с кэшированием и автоматическим обновлением"""
        if not force_refresh and self.access_token and datetime.utcnow() < self.token_expiry:
            return self.access_token
        
        self.transport_token = await self._generate_transport_token()
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.salutejazz.ru/v1/auth/login",
                headers={"Authorization": f"Bearer {self.transport_token}"}
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    raise ConnectionError(f"Login failed: {response.status} - {text}")
                
                data = await response.json()
                self.access_token = data["token"]
                self.token_expiry = datetime.utcnow() + timedelta(minutes=10)
                return self.access_token

    def extract_room_id_from_url(self, room_url: str) -> str:
        """Извлекает roomId из URL комнаты"""
        # Извлекаем идентификатор комнаты из URL, разрешаем дефисы и другие возможные символы
        match = re.search(r'/([^/?]+)(?:\?|$)', room_url)
        if not match:
            raise ValueError("Не удалось извлечь roomId из URL")
            
        return match.group(1)


    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
        reraise=True
    )
    async def get_transcriptions(self, room_url: str, limit: int = 10000, offset: int = 0) -> dict:
        """Получает транскрипции для указанной комнаты"""
        await self._get_access_token()
        
        room_id = self.extract_room_id_from_url(room_url)
        
        url = f"https://api.salutejazz.ru/v1/room/{room_id}/transcriptions"
        headers = {
            "Authorization": f"Bearer {self.access_token}"
        }
        
        params = {
            "limit": limit,
            "offset": offset
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as response:
                if response.status == 401:
                    await self._get_access_token(force_refresh=True)
                    return await self.get_transcriptions(room_url, limit, offset)
                
                if response.status != 200:
                    text = await response.text()
                    raise ConnectionError(f"Get transcriptions failed: {response.status} - {text}")
                
                return await response.json()


async def main():
    # Инициализация API
    jazz = SberJazzAPI(
        sdk_key_base64=os.getenv("JAZZ_SDK"),  # Ключ из переменной окружения
        user_id="tournament_bot_system"
    )
    
    # URL комнаты для получения транскрипции
    room_url = "https://salutejazz.ru/xvpn9b?psw=OEcYHBYJUkYQVx8KSR1FVR9dAg"
    
    try:
        # Получаем транскрипции
        transcriptions = await jazz.get_transcriptions(room_url)
        print("Транскрипции получены успешно:")
        print(json.dumps(transcriptions, indent=2, ensure_ascii=False))
        
        # Сохраняем в файл
        with open("transcriptions.json", "w", encoding="utf-8") as f:
            json.dump(transcriptions, f, indent=2, ensure_ascii=False)
        print("\nТранскрипции сохранены в transcriptions.json")
        
    except Exception as e:
        print(f"Ошибка при получении транскрипций: {e}")


if __name__ == "__main__":
    asyncio.run(main())
