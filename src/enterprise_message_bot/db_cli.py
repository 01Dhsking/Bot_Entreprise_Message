import argparse
import asyncio

from .database import wait_for_database
from .logging_config import configure_logging


async def main() -> None:
    parser = argparse.ArgumentParser(description="Database helpers")
    parser.add_argument("command", choices=["wait"])
    args = parser.parse_args()
    if args.command == "wait":
        await wait_for_database()


if __name__ == "__main__":
    configure_logging()
    asyncio.run(main())
