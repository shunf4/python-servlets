from . import handler

async def handle():
    return handler.SubscriptionResponder().execute()

