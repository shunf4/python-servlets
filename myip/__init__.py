from quart import request

async def handle():
    return request.headers.get("X-Real-IP", request.remote_addr) + "\r\n", 200, {
        "Content-Type": "text/plain; charset=utf-8"
    }