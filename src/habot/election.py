import asyncio
import uuid
import logging
import etcd3

from typing import Optional, Callable, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(filename)s:%(lineno)d] %(message)s")

def extrect_host_port(s:str):
    l = s.split(":")
    return l[0], int(l[1])


class ElectionConfig:
    def __init__(self, service_name: str, etcd_hosts: list,
                 lease_ttl: int = 15,
                 health_check_interval: int = 5):
        self.service_name = service_name
        self.etcd_hosts = etcd_hosts
        self.lease_ttl = lease_ttl
        self.health_check_interval = health_check_interval


class LongPollingLeaderElection:
    def get_client(self, hosts):
        for host_url in hoats:
            logger.info (f"trying  to connect {host_url}")
            try:
                host, port = extrect_host_port(config.host)
                return etcd3.client(host=host, port=port)
            except Exception as ex:
                logger.error(f"trying  to connect {host_url} {ex}")


    def __init__(
            self,
            config: ElectionConfig,
            on_elected_callback: Callable = None,
            on_slave_callback: Callable = None,
    ):
        #host, port = extrect_host_port(config.etcd_hosts[0])

        self.etcd = self.get_client(config.etcd_hosts)

        self.lease_ttl = config.lease_ttl
        self.health_check_interval = config.health_check_interval

        # Идентификаторы
        self.instance_id = f"{uuid.uuid4()}"
        self.service_name = f"telegram_bot_{config.service_name}"

        # Состояние
        self.is_leader = False
        self.lease: Optional[etcd3.Lease] = None
        self.offset = 0

        # Ключи в etcd
        self.leader_key = f"/bots/{self.service_name}/leader"
        self.offset_key = f"/bots/{self.service_name}/offset"
        self.health_key = f"/bots/{self.service_name}/health/{self.instance_id}"
        self.config_prefix = f"/bots/{self.service_name}/config/"

        # Callbacks
        self.on_leader_elected: Optional[Callable] = on_elected_callback
        self.on_leader_lost: Optional[Callable] = on_slave_callback
        self.on_update_received: Optional[Callable] = None

        # Статистика
        self.stats = {
            'start_time': datetime.now(),
            'updates_processed': 0,
            'leadership_changes': 0
        }

    async def start(self):
        """Запуск leader election"""
        logger.info(f"Starting leader election for instance {self.instance_id}")

        # Регистрация инстанса
        await self._register_instance()

        # Основной цикл
        while True:
            try:
                if not self.is_leader:
                    await self._try_acquire_leadership()
                else:
                    await self._maintain_leadership()

                # Health check
                await self._update_health_status()
                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Leader election error: {e}")
                await self._release_leadership()
                await asyncio.sleep(5)

    async def _register_instance(self):
        """Регистрация инстанса в etcd"""
        try:
            # Сохраняем информацию об инстансе
            instance_info = {
                'id': self.instance_id,
                'start_time': datetime.now().isoformat(),
                'status': 'starting',
                'last_seen': datetime.now().isoformat()
            }

            self.etcd.put(
                self.health_key,
                str(instance_info),
                lease=self.etcd.lease(30)
            )

            logger.info(f"Instance {self.instance_id} registered")

        except Exception as e:
            logger.error(f"Failed to register instance: {e}")

    async def _try_acquire_leadership(self):
        """Попытка стать лидером"""
        try:
            # Проверяем текущего лидера
            current_leader, _ = self.etcd.get(self.leader_key)

            if current_leader:
                leader_id = current_leader.decode()

                # Проверяем здоровье лидера
                leader_health_key = f"/bots/{self.service_name}/health/{leader_id}"
                leader_health, _ = self.etcd.get(leader_health_key)

                if not leader_health:
                    logger.warning(f"Leader {leader_id} appears dead, attempting takeover")
                    self.etcd.delete(self.leader_key)
                else:
                    # Лидер жив, ждем
                    return

            # Создаем новый lease
            self.lease = self.etcd.lease(self.lease_ttl)

            # Пытаемся захватить лидерство
            success = self.etcd.put_if_not_exists(
                self.leader_key,
                self.instance_id,
                lease=self.lease
            )

            if success:
                self.is_leader = True
                self.stats['leadership_changes'] += 1
                logger.info(f"Instance {self.instance_id} became leader")

                # Загружаем последний offset
                await self._load_offset()

                # Вызываем callback
                if self.on_leader_elected:
                    await self.on_leader_elected()

        except Exception as e:
            logger.error(f"Failed to acquire leadership: {e}")
            if self.lease:
                self.lease.revoke()

    async def _maintain_leadership(self):
        """Поддержание лидерства"""
        try:
            # Обновляем lease
            if self.lease:
                self.lease.refresh()

            # Обновляем статус
            instance_info = {
                'id': self.instance_id,
                'status': 'leader',
                'last_seen': datetime.now().isoformat(),
                'offset': self.offset,
                'updates_processed': self.stats['updates_processed']
            }

            self.etcd.put(
                self.health_key,
                str(instance_info),
                lease=self.etcd.lease(self.lease_ttl)
            )

        except Exception as e:
            logger.error(f"Failed to maintain leadership: {e}")
            await self._release_leadership()

    async def _release_leadership(self):
        """Освобождение лидерства"""
        if self.is_leader:
            try:
                self.etcd.delete(self.leader_key)
            except:
                pass

            self.is_leader = False

            logger.info(f"Instance {self.instance_id} released leadership")
            if self.on_leader_lost:
                await self.on_leader_lost()

    async def _load_offset(self):
        """Загрузка последнего обработанного offset из etcd"""
        try:
            offset_data, _ = self.etcd.get(self.offset_key)
            if offset_data:
                self.offset = int(offset_data.decode())
                logger.info(f"Loaded offset: {self.offset}")
        except:
            self.offset = 0
            logger.info("No offset found, starting from 0")

    async def save_offset(self, offset: int):
        """Сохранение offset в etcd"""
        try:
            self.offset = offset
            self.etcd.put(self.offset_key, str(offset))

            # Обновляем статистику
            self.stats['updates_processed'] += 1

        except Exception as e:
            logger.error(f"Failed to save offset: {e}")

    async def _update_health_status(self):
        """Обновление статуса здоровья"""
        try:
            status = 'leader' if self.is_leader else 'follower'
            health_data = {
                'id': self.instance_id,
                'status': status,
                'last_seen': datetime.now().isoformat(),
                'is_leader': self.is_leader,
                'offset': self.offset
            }

            self.etcd.put(
                self.health_key,
                str(health_data),
                lease=self.etcd.lease(30)
            )

        except Exception as e:
            logger.error(f"Failed to update health status: {e}")

    def get_cluster_status(self) -> Dict[str, Any]:
        """Получение статуса кластера"""
        try:
            status = {
                'current_leader': None,
                'active_instances': [],
                'is_leader': self.is_leader,
                'instance_id': self.instance_id,
                'offset': self.offset
            }

            # Получаем текущего лидера
            leader, _ = self.etcd.get(self.leader_key)
            if leader:
                status['current_leader'] = leader.decode()

            # Получаем активные инстансы
            for value, metadata in self.etcd.get_prefix(f"/bots/{self.service_name}/health/"):
                try:
                    instance_data = eval(value.decode()) if value else {}
                    status['active_instances'].append(instance_data)
                except:
                    pass

            return status

        except Exception as e:
            logger.error(f"Failed to get cluster status: {e}")
            return {}

    async def stop(self):
        """Остановка"""
        await self._release_leadership()

        # Удаляем health key
        try:
            self.etcd.delete(self.health_key)
        except:
            pass
        logger.info(f"Instance {self.instance_id} stopped")
