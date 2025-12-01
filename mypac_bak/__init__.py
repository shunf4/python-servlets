import urllib.parse
import os
import aiofiles
from quart import request

from . import config

async def handle():
    query_string = request.query_string.decode("utf-8")
    query_dict = urllib.parse.parse_qs(query_string)
    
    proxy_endpoint = query_dict.get("endpoint", ["SOCKS5 127.0.0.1:1079; SOCKS 127.0.0.1:1079"])[0]
    netease_host = query_dict.get("netease", ["DIRECT"])[0]
    netease_new = query_dict.get("neteasenew", ["DIRECT"])[0]
    proxy_port = query_dict.get("port", ["NONE"])[0]
    proxy_http_port = query_dict.get("httpport", ["NONE"])[0]

    if proxy_http_port.lower() != "none":
        proxy_endpoint = "PROXY 127.0.0.1:%s; PROXY 127.0.0.1:%s" % (proxy_http_port, proxy_http_port)

    if proxy_port.lower() != "none":
        proxy_endpoint = "SOCKS5 127.0.0.1:%s; SOCKS 127.0.0.1:%s" % (proxy_port, proxy_port)

    if netease_host.lower() != "direct":
        netease_endpoint = "PROXY " + netease_host + ":32222"
    else:
        netease_endpoint = netease_host
        
    if netease_new.lower() != "direct":
        netease_endpoint = "SOCKS5 " + netease_new + "; " + "SOCKS " + netease_new + "; " + netease_endpoint

    old_cwd = os.getcwd()
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    async with aiofiles.open(config.PAC_TEMPLATE_FILE_PATH, mode="r", encoding="utf-8") as f:
        pac_template = await f.read()
    os.chdir(old_cwd)

    return pac_template.replace("__PROXY__", proxy_endpoint).replace("__NETEASE__", netease_endpoint), 200, {
#        "Content-Type": "application/x-ns-proxy-autoconfig; charset=utf-8"
        "Content-Type": "text/plain"
    }

