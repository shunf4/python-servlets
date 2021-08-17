from . import handler
import sys

async def handle():
    return handler.SubscriptionResponder().execute()

