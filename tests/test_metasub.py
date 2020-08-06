import sys
sys.path.insert(0, "..")
from metasub import handler
import asyncio

async def main():
    query_string = sys.stdin.read()
    stream, status_code = handler.SubscriptionResponder(query_string).execute()
    print(f"Status code: {status_code}")

    chunk: bytes
    async for chunk in stream:
        sys.stdout.write(b"output: " + chunk + b"\n")


asyncio.run(main())