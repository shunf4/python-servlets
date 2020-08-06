from aiohttp_socks import SocksConnector, SocksVer, ProxyConnector, ProxyType

KEY_PATH_MAP = {
    "secretdonttellothers": "./sub.txt",
}

METASUB_TIMEOUT_TOTAL_SEC = 13
SUB_TIMEOUT_TOTAL_SEC = 30

def create_connector_for_metasub(url: str):
    proxy_connector = ProxyConnector(
        proxy_type = ProxyType.SOCKS5,
        host = '127.0.0.1',
        port = 1080,
        rdns = True
    )
    direct_connector = None
    return direct_connector
    
def create_connector_for_sub(url: str):
    proxy_connector = ProxyConnector(
        proxy_type = ProxyType.SOCKS5,
        host = '127.0.0.1',
        port = 1080,
        rdns = True
    )
    direct_connector = None
    return direct_connector
