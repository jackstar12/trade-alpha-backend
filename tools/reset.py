import asyncio
import pathlib
from pathlib import Path
import toml
import argparse
from database import dbmodels
import database.dbasync as db
from database.utils import reset_client


async def reset(client_id: int, full=False):
    client = await db.async_session.get(dbmodels.Client, client_id)
    if client:
        await reset_client(client_id, db.async_session, full=full)
    else:
        print('Invalid ID!')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Reset a specific client")
    parser.add_argument("--id", help="Client id to reset", type=int)
    parser.add_argument("--full", help="Client id to reset", type=bool, default=False)
    args = parser.parse_args()
    asyncio.run(reset(args.id, args.full))
