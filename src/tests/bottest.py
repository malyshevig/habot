import asyncio
import unittest

from aiogram.types import Update

from habot import HaBot, ElectionConfig


class TestBot(HaBot):
    def __init__(self, token: str):
        self.token = token
        self.election_config = ElectionConfig(
            "bot_test", etcd_hosts=["localhost"]
        )
        super().__init__(self.token, self.election_config)

    async def process_update(self, update: Update):
        if update.message is not None:
            await self.send_message(update.message.chat.id, "Message Accepted")


BOT_TOKEN = "8535593950:AAHGhZ4mRK7LWWl2Q63-c5iC7aKS0E3gWJ4"


class MyTestCase(unittest.TestCase):
    def test_something(self):
        self.bot = TestBot(BOT_TOKEN)
        asyncio.run(self.bot.start())


if __name__ == '__main__':
    unittest.main()
