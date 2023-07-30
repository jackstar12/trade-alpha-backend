import asyncio
import argparse
from typing import Any

from sqlalchemy import select

from common.messenger import Messenger, NameSpaceInput
from database.dbasync import db_all, redis, db_unique


async def publish(namespace: NameSpaceInput, topic: str, id: Any = None):
    messenger = Messenger(redis)
    namespace = messenger.get_namespace(namespace)

    instance = await db_unique(
        select(namespace.table).where(
            namespace.table.id == id,
        ),
        getattr(namespace.table, 'client', None),
        getattr(namespace.table, 'user', None),
    )
    print(instance, namespace.table, id)
    await messenger.pub_instance(
        instance,
        topic,
    )


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Reset a specific client")
    parser.add_argument("--ns", help="Namespace", type=str)
    parser.add_argument("--topic", help="Topic to publish", type=str)
    parser.add_argument("--id", help="Id of the instance", type=int, required=False)
    args = parser.parse_args()
    asyncio.run(publish(args.ns, args.topic, args.id))
