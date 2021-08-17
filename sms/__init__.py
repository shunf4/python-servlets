from quart import request
from register import GLOBAL_REGISTER

async def handle():
    global CURRENT_SMS
    if request.method == "GET":
        return GLOBAL_REGISTER.get("CURRENT_SMS", "uninitialized")

    if request.method == "POST":
        j = await request.get_json()
        GLOBAL_REGISTER["CURRENT_SMS"] = j["from"] + "_" + j["text"]
        return "success"

