from abc import ABC, abstractmethod
import json
import asyncio
from typing import Dict, Any, Optional, List
from aiogram import Bot

import aiohttp
import logging

from aiogram.types import Update

from .election import ElectionConfig, LongPollingLeaderElection

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(filename)s:%(lineno)d] %(message)s")


class HaBot(ABC):
    def __init__(self, token, config: ElectionConfig,
                 polling_timeout: int = 30,
                 polling_limit: int = 100, allowed_updates: Optional[List[str]] = None
                 ):
        self.token = token
        self.config = config
        self.election = LongPollingLeaderElection(config, self._on_leader_elected, self._on_leader_lost)
        self.bot = Bot(token=token)

        self.is_running = False
        self.offset = 0
        self.polling_timeout = polling_timeout
        self.polling_limit = polling_limit
        self.allowed_updates = allowed_updates or [
            "message"
        ]

    async def start(self):
        if self.is_running:
            return

        self.is_running = True
        logger.info(f"Starting bot instance: {self.election.instance_id}")

        try:
            # Запускаем leader election
            await self.election.start()
        except Exception as e:
            logger.error(f"Bot failed to start: {e}")
            await self.stop()

    async def stop(self):
        """Остановка бота"""
        self.is_running = False

        logger.info("Stopping bot...")

        # Останавливаем leader election
        await self.election.stop()

        # Останавливаем polling
        if self.polling_task and not self.polling_task.done():
            self.polling_task.cancel()
            try:
                await self.polling_task
            except asyncio.CancelledError:
                pass

        # Закрываем сессию
        if self.session and not self.session.closed:
            await self.session.close()

        # Закрываем бота
        await self.bot.session.close()

        logger.info("Bot stopped")

    async def _on_leader_elected(self):
        """Callback при избрании лидером"""
        logger.info("I am now the leader, starting polling...")

        # Создаем HTTP сессию
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.polling_timeout + 10)
        )

        # Запускаем polling
        self.polling_task = asyncio.create_task(self._polling_loop())

    async def _on_leader_lost(self):
        """Callback при потере лидерства"""
        logger.info("I am no longer the leader, stopping polling...")

        # Останавливаем polling
        if self.polling_task and not self.polling_task.done():
            self.polling_task.cancel()
            try:
                await self.polling_task
            except asyncio.CancelledError:
                pass

        # Закрываем сессию
        if self.session and not self.session.closed:
            await self.session.close()

    async def _polling_loop(self):
        """Основной цикл long polling"""
        logger.info("Starting long polling loop")

        while self.election.is_leader and self.is_running:
            try:
                # Получаем обновления
                updates = await self._get_updates2()

                if updates:
                    await self.process_updates(updates)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Polling error: {e}")

                await asyncio.sleep(5)

    async def _get_updates(self) -> Dict[str, Any]:
        """Получение обновлений через Telegram API"""
        try:
            # Формируем параметры запроса
            params = {
                'offset': self.offset + 1,
                'timeout': self.polling_timeout,
                'limit': self.polling_limit,
                'allowed_updates': json.dumps(self.allowed_updates)
            }
            url = f"https://api.telegram.org/bot{self.token}/getUpdates"

            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                else:
                    logger.error(f"API error: {response.status}")
                    return {'ok': False, 'result': []}

        except aiohttp.ClientError as e:
            logger.error(f"HTTP error: {e}")
            return {'ok': False, 'result': []}
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return {'ok': False, 'result': []}

    async def _get_updates2(self) -> List[Update]:
        """Получение обновлений через Telegram API"""
        try:
            updates = await self.bot.get_updates(offset=self.offset + 1, timeout=self.polling_timeout,
                                                 limit=self.polling_limit, allowed_updates=self.allowed_updates)
            return updates
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            raise Exception(e)

    @abstractmethod
    async def process_update(self, update):
        pass

    async def process_updates(self, updates: List[Update]):
        for update in updates:
            try:
                update_id = update.update_id
                # Обрабатываем update
                await self.process_update(update)

                # Обновляем offset
                if update_id > self.offset:
                    self.offset = update_id
                    await self.election.save_offset(update_id)

            except Exception as e:
                logger.exception(f"Error processing update: {e}")
                raise Exception(e)

    async def send_message(self, chat_id, text, **kwargs):
        """Отправка сообщения"""
        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                **kwargs
            )
        except Exception as e:
            logger.exception(f"Failed to send message: {e}")
