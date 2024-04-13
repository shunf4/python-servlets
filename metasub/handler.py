import base64
import yaml
import json
import aiohttp
import asyncio
import aiofiles
import urllib.parse
import re
import os
import sys
import itertools
import contextlib
import datetime
import copy
import traceback
from quart import request
from typing import List, Dict

from . import config

def is_flag_value_enabled(value: str) -> bool:
    return value == "1" or value.lower() == "true"

class SubscriptionResponder(object):
    class ResponseBodyIterable:
        def __init__(self):
            self.queue = asyncio.Queue()
            
        def __aiter__(self):
            return self

        def write(self, sth: str):
            self.queue.put_nowait(sth)

        def end(self):
            self.queue.put_nowait(StopAsyncIteration("response body end"))

        async def __anext__(self):
            data = await self.queue.get()
            self.queue.task_done()

            if isinstance(data, StopAsyncIteration):
                raise data
            elif isinstance(data, str):
                return data.encode("utf-8")
            elif isinstance(data, bytes) or isinstance(data, bytearray):
                return data
            else:
                raise TypeError(f"invalid type received by body_writer: {type(data).__name__}")

    def base64_decode(self, x):
        return base64.urlsafe_b64decode(x + '=' * (-len(x) % 4)).decode("utf-8")

    def urlsafe_base64_decode(self, x):
        return base64.urlsafe_b64decode(x + '=' * (-len(x) % 4)).decode("utf-8")

    # Base64 decoder that is compatible with both urlsafe and non-urlsafe formats.
    def compat_base64_decode(self, x):
        self.debug(f"Base64 decoding: {x}")
        try:
            return self.base64_decode(x)
        except Exception:
            return self.urlsafe_base64_decode(x)

    def urlsafe_base64_encode(self, x):
        self.debug(f"Base64 urlsafe encoding: {x}")
        r = base64.urlsafe_b64encode(str(x).encode("utf-8")).decode("ascii")
        while r and r[-1] == '=':
            r = r[:-1]
        self.debug(f"After base64 urlsafe encoding: {r}")
        return r

    def base64_encode(self, x):
        self.debug(f"Base64 encoding: {x}")
        r = base64.b64encode(str(x).encode("utf-8")).decode("ascii")
        while r and r[-1] == '=':
            r = r[:-1]
        self.debug(f"After base64 encoding: {r}")
        return r

    def __init__(self, overriding_query_string=None):
        self.clash_base = { "proxies": [] }
        self.entries = []

        if overriding_query_string and isinstance(overriding_query_string, str):
            self.query_dict = urllib.parse.parse_qs(overriding_query_string)
        else:
            self.query_dict = urllib.parse.parse_qs(request.query_string.decode("utf-8"))
        
        query_debug = self.query_dict.get("debug", ["0"])[0]
        query_d = self.query_dict.get("d", ["0"])[0]
        query_group = self.query_dict.get("group", ["0"])[0]
        query_usecache = self.query_dict.get("usecache", ["0"])[0]
        self.is_debug = any(map(is_flag_value_enabled, [query_debug, query_d]))
        self.is_grouping_enabled = is_flag_value_enabled(query_group)
        self.is_use_cache = is_flag_value_enabled(query_usecache)

        self.clashray_curr_as_publisher = self.query_dict.get("clashray_curr_as_publisher", [""])[0]
        self.clashray_curr_is_as_visitor = self.query_dict.get("clashray_curr_is_as_visitor", [""])[0]
        self.clashray_send_dir = self.query_dict.get("clashray_send_dir", [""])[0]
        self.clashray_android_transports = self.query_dict.get("clashray_android_transports", [""])[0]
        if self.clashray_curr_is_as_visitor == "true":
            self.clashray_curr_is_as_visitor = "1"
        if self.clashray_curr_is_as_visitor == "false":
            self.clashray_curr_is_as_visitor = "0"

        if "url" in self.query_dict:
            query_url = self.query_dict["url"][0]
            self.metasub_location = query_url
            self.metasub_location_type = "URL"
        else:
            query_key = self.query_dict.get("key", [""])[0]
            if query_key in config.KEY_PATH_MAP:
                self.metasub_location = config.KEY_PATH_MAP[query_key]
                if self.metasub_location.startswith("http://") or self.metasub_location.startswith("https://"):
                    self.metasub_location_type = "URL"
                else:
                    self.metasub_location_type = "FILEPATH"
            else:
                raise ValueError(f"No such key: {query_key if query_key else '(empty)'}")

        self.out_type = self.query_dict.get("type", ["v2"])[0]

        if self.out_type not in ["v2", "v2ss", "clash", "json", "ssr", "v2sip"]:
            raise ValueError(f"out_type({self.out_type}) not in [v2, v2ss, clash, json, ssr, v2sip]")

        self.is_clashray = is_flag_value_enabled(self.query_dict.get("clashray", ["0"])[0])
        self.in_external_scope = list(map(lambda x: x.strip(), self.query_dict.get("inextscope", [""])[0].split(",")))
        self.in_lan_scope = list(map(lambda x: x.strip(), self.query_dict.get("inlanscope", [""])[0].split(",")))
        self.in_self_scope = list(map(lambda x: x.strip(), self.query_dict.get("inselfscope", [""])[0].split(",")))
        self.in_none_scope = list(map(lambda x: x.strip(), self.query_dict.get("innonescope", [""])[0].split(",")))

        self.body_writer: SubscriptionResponder.ResponseBodyIterable = None

    def debug(self, msg, *args, **kwargs):
        args = list(args)
        args.insert(0, msg)
        output_str = " ".join(map(lambda x: x if isinstance(x, str) else repr(x), args))
        # print("METASUB-DEBUG: " + output_str, file=sys.stderr)
        if self.is_debug and self.body_writer:
            self.body_writer.write("DEBUG: " + output_str + "\n")
    
    def error(self, msg, *args, **kwargs):
        args = list(args)
        args.insert(0, msg)
        output_str = " ".join(map(lambda x: x if isinstance(x, str) else repr(x), args))
        print("METASUB-ERROR: " + output_str, file=sys.stderr)
        if self.body_writer:
            self.body_writer.write('$%*"@#$% ERROR: ' + output_str + "\n")

    async def get_metasub(self):
        if self.metasub_location_type == "URL":
            async with aiohttp.ClientSession(connector=config.create_connector_for_metasub(self.metasub_location), timeout=aiohttp.ClientTimeout(total=config.METASUB_TIMEOUT_TOTAL_SEC)) as session:
                self.debug(f"Getting metasub from", self.metasub_location)
                resp = await session.get(self.metasub_location)
                raw_metasub = await resp.text()
                self.debug(f"Got metasub ({len(raw_metasub)} chars)")
        elif self.metasub_location_type == "FILEPATH":
            old_cwd = os.getcwd()
            os.chdir(os.path.dirname(os.path.abspath(__file__)))
            async with aiofiles.open(self.metasub_location, mode="r", encoding="utf-8") as f:
                self.debug(f"Reading metasub from", self.metasub_location)
                raw_metasub = await f.read()
                self.debug(f"Got metasub ({len(raw_metasub)} chars)")
            os.chdir(old_cwd)

        metasub = yaml.safe_load(raw_metasub)
        
        if not isinstance(metasub, dict):
            raise TypeError(f"metasub is not a dict after json deserialization: {type(metasub).__name__}")
        if not isinstance(metasub.get("clash-base", {}), dict):
            raise TypeError(f"metasub[clash-base] is not a dict after json deserialization: {type(metasub.get('clash-base', {})).__name__}")
        if not isinstance(metasub.get("clash-base", {}).get("proxies"), list):
            raise TypeError(f"metasub[clash-base][proxies] is not a list after json deserialization: {type(metasub.get('clash-base', {}).get('entries')).__name__}")
        if not isinstance(metasub.get("entries"), list):
            raise TypeError(f"metasub[entries] is not a list after json deserialization: {type(metasub.get('entries')).__name__}")

        self.clash_base = metasub.get("clash-base", self.clash_base)
        self.entries = metasub.get("entries")

    async def get_proxies_from_sub_url(self, url, filter, exclude_filter, allow_ss, allow_ssr):
        result = []
        if self.is_use_cache:
            self.debug(f"Getting sub from", url, " (from state cache)")
            raw_sub = self.state.get("CACHED_SUB_URL_CONTENT_" + url, "")
            raw_sub_time = self.state.get("CACHED_SUB_URL_TIME_" + url, "")
            if raw_sub_time != "":
                self.debug(url, " has cache, cached at " + raw_sub_time)
                result = [{
                        "is_ss": True,
                        "add": "127.0.0.1",
                        "port": 11111,
                        "enc": "aes-256-gcm",
                        "password": "a",
                        "ps": "Cached at " + raw_sub_time
                    }]
        else:
            async with aiohttp.ClientSession(connector=config.create_connector_for_sub(url), timeout=aiohttp.ClientTimeout(total=config.SUB_TIMEOUT_TOTAL_SEC)) as session:
                self.debug(f"Getting sub from", url)
                resp = await session.get(url)
                raw_sub = await resp.text()
                self.debug(f"Got sub from {url} ({len(raw_sub)} chars)")
                self.state["CACHED_SUB_URL_CONTENT_" + url] = raw_sub
                self.state["CACHED_SUB_URL_TIME_" + url] = str(datetime.datetime.now())

        if raw_sub.startswith("ssd://"):
            proxies = []
            ssd_obj = json.loads(self.base64_decode(raw_sub[len("ssd://"):]))
            common_ss_obj = {
                "is_ss": True,
                "port": ssd_obj["port"],
                "enc": ssd_obj["encryption"],
                "password": ssd_obj["password"],
            }
            for ss in ssd_obj["servers"]:
                ss_obj = {**common_ss_obj, **{"add": ss["server"], "ps": ss["remarks"]}}
                if "port" in ss:
                    ss_obj["port"] = ss["port"]
                if "password" in ss:
                    ss_obj["password"] = ss["password"]
                if "encryption" in ss:
                    ss_obj["enc"] = ss["encryption"]
                if "plugin" in ss and "plugin_options" in ss:
                    ss_plugin_parts = ss["plugin_options"].split(";")
                    ss_plugin_parse_result = {}
                    for part in ss_plugin_parts:
                        part_kv = part.split("=")
                        if len(part_kv) != 2:
                            continue
                        
                        k, v = part_kv
                        if k == "obfs":
                            ss_plugin_parse_result["mode"] = v
                        elif k == "obfs-host":
                            ss_plugin_parse_result["host"] = v
                    if ss_plugin_parse_result:
                        ss_obj["plugin"] = "obfs"
                        ss_obj["plugin-opts"] = ss_plugin_parse_result
                self.debug("Adding proxy: " + json.dumps(ss_obj, ensure_ascii=False))
                proxies.append(ss_obj)
        elif raw_sub == "" or raw_sub == b"":
            proxies = []
        else:
            proxies = self.base64_decode(raw_sub).split("\n")

        filter_regex = re.compile(filter)
        exclude_regex = re.compile(exclude_filter)

        for proxy in proxies:
            if isinstance(proxy, dict):
                if filter_regex.fullmatch(proxy['ps']) and not exclude_regex.fullmatch(proxy['ps']):
                    result.append(proxy)
            elif proxy.startswith("vmess://"):
                proxy = proxy[len("vmess://"):]
                proxy = json.loads(self.base64_decode(proxy))
                if filter_regex.fullmatch(proxy['ps']) and not exclude_regex.fullmatch(proxy['ps']):
                    result.append(proxy)
            elif proxy.startswith("ss://") and allow_ss:
                # ss://cmM0LW1kNTpwYXNzd2Q=@192.168.100.1:8888/?plugin=obfs-local%3Bobfs%3Dhttp#Example2
                split_by_hash = proxy[len("ss://"):].split('#')
                ps = urllib.parse.unquote(split_by_hash[-1])
                if len(split_by_hash) < 2 or filter_regex.fullmatch(ps) and not exclude_regex.fullmatch(ps):
                    ss_body = '#'.join(split_by_hash[:-1])
                    ss_ps = "SSServer" if len(split_by_hash) < 2 else ps.strip()
                    if not ('@' in ss_body):
                        ss_body = self.compat_base64_decode(ss_body)

                    split_by_at = ss_body.split("@")
                    if ':' in split_by_at[0]:
                        ss_user = split_by_at[0]
                    else:
                        ss_user = self.compat_base64_decode(split_by_at[0])

                    ss_enc, ss_password = ss_user.split(":")
                    ss_add, ss_port = split_by_at[1].split(":")

                    ss_port = ''.join(itertools.takewhile(str.isdigit, ss_port))
                    
                    # obfs plugin
                    
                    ss_body_query_parts = ss_body.split("?")
                    ss_plugin_parse_result = {}

                    if len(ss_body_query_parts) >= 2:
                        ss_body_query_str = '?'.join(ss_body_query_parts[1:])
                        ss_body_query = dict(urllib.parse.parse_qsl(ss_body_query_str))
                        ss_plugin = ss_body_query.get("plugin", "")
                        
                        if ss_plugin != "":
                            ss_plugin_parts = ss_plugin.split(";")
                            for part in ss_plugin_parts:
                                part_kv = part.split("=")
                                if len(part_kv) <= 0:
                                    continue
                                elif len(part_kv) == 1:
                                    if part_kv[0] != "obfs-local":
                                        ss_plugin_parse_result = {}
                                        break
                                    else:
                                        continue
                                elif len(part_kv) > 2:
                                    continue
                                
                                k, v = part_kv
                                if k == "obfs":
                                    ss_plugin_parse_result["mode"] = v
                                elif k == "obfs-host":
                                    ss_plugin_parse_result["host"] = v
                        

                    ss_obj = {
                        "is_ss": True,
                        "add": ss_add,
                        "port": ss_port,
                        "enc": ss_enc,
                        "password": ss_password,
                        "ps": ss_ps
                    }
                    
                    if ss_plugin_parse_result:
                        ss_obj["plugin"] = "obfs"
                        ss_obj["plugin-opts"] = ss_plugin_parse_result

                    if self.is_debug:
                        ss_obj.update({"origin": proxy})
                        self.debug(f"Added SS proxy: {repr(ss_obj)}")
                    
                    result.append(ss_obj)
            elif proxy.startswith("ssr://") and allow_ssr:
                # ssr://base64(host:port:protocol:method:obfs:base64pass/?obfsparam=base64param&protoparam=base64param&remarks=base64remarks&group=base64group&udpport=0&uot=0)
                # https://github.com/HMBSbige/ShadowsocksR-Windows/wiki/SSR-QRcode-scheme
                proxy = proxy[len("ssr://"):]
                proxy = self.base64_decode(proxy)
                split_by_slash = proxy.split('/?')
                ssr_basic_info = split_by_slash[0]
                ssr_extra_param_str = ""
                ssr_ps = "SSRServer"
                if len(split_by_slash) > 1:
                    ssr_extra_param_str = "/?".join(split_by_slash[1:])

                [
                    ssr_host,
                    ssr_port,
                    ssr_protocol,
                    ssr_method,
                    ssr_obfs,
                    ssr_base64pass
                ] = ssr_basic_info.split(":")
                ssr_pass = self.base64_decode(ssr_base64pass)

                ssr_extra_param = dict(urllib.parse.parse_qsl(ssr_extra_param_str))
                ssr_extra_param_result = {}

                for k, v in ssr_extra_param.items():
                    if k == "obfsparam":
                        ssr_extra_param_result["obfs-param"] = self.base64_decode(v)
                    elif k == "protoparam":
                        ssr_extra_param_result["protocol-param"] = self.base64_decode(v)
                    elif k == "remarks":
                        ssr_ps = self.base64_decode(v)

                ssr_obj = {
                    "is_ssr": True,
                    "add": ssr_host,
                    "port": ssr_port,
                    "enc": ssr_method,
                    "password": ssr_pass,
                    "ps": ssr_ps,
                    "obfs": ssr_obfs,
                    "protocol": ssr_protocol,
                    **ssr_extra_param_result
                }

                if self.is_debug:
                    ssr_obj.update({"origin": proxy})
                    self.debug(f"Added SSR proxy: {repr(ssr_obj)}")
                
                result.append(ssr_obj)
            else:
                if self.is_debug:
                    self.debug(f"Unrecognized proxy: {repr(proxy)}")
        return result

    async def get_proxies_as_is(self, entry):
        return [entry]

    async def fetch_raw_proxies(self) -> List[Dict]:
        tasks: List[asyncio.Future] = []
        allow_ss: bool = self.out_type in ["v2ss", "clash", "json", "ssr", "v2sip"]
        allow_ssr: bool = self.out_type in ["clash", "json", "ssr"]
        for entry in self.entries:
            if not isinstance(entry, dict):
                raise TypeError(f"{repr(entry)} is not a dict: {type(entry).__name__}")
            subscribe_url = entry.get("subscribe_url")
            if subscribe_url is not None:
                tasks.append(self.get_proxies_from_sub_url(
                    subscribe_url,
                    entry.get("filter", r'^.*$'),
                    entry.get("exclude_filter", r'^$'),
                    allow_ss,
                    allow_ssr
                ))
            else:
                tasks.append(self.get_proxies_as_is(entry))

        raw_proxies_grouped = await asyncio.gather(*tasks)
        self.debug(f"raw_proxies_grouped: {json.dumps(raw_proxies_grouped, ensure_ascii=False)}")
        # flatten
        if self.is_grouping_enabled:
            raw_proxies = [{
                **proxy,
                **{"group": self.entries[i].get("subscribe_url", "//No group").split("/")[2]},
                **{"mux": True},
                **{"muxConcurrency": 16},
            } for i, group in enumerate(raw_proxies_grouped) for proxy in group]
        else:
            raw_proxies = [{
                **proxy,
                **{"group": "No group"},
                **{"mux": True},
                **{"muxConcurrency": 16},
            } for i, group in enumerate(raw_proxies_grouped) for proxy in group]

        return raw_proxies

    def get_clash_result(self, raw_proxies: List[Dict]) -> str:
        clash_result = copy.deepcopy(self.clash_base)

        if self.clashray_curr_as_publisher is not None and self.clashray_curr_as_publisher != "":
            clash_result["clashray-net-curr-as-publisher"] = self.clashray_curr_as_publisher

        if self.clashray_curr_is_as_visitor == "0":
            clash_result["clashray-net-curr-is-as-visitor"] = False
        elif self.clashray_curr_is_as_visitor == "1":
            clash_result["clashray-net-curr-is-as-visitor"] = True

        if self.clashray_send_dir is not None and self.clashray_send_dir != "":
            clash_result["clashray-send-dir"] = self.clashray_send_dir
        if self.clashray_android_transports is not None and self.clashray_android_transports != "":
            clash_result["reverse-enable-on-android-type-transports"] = list(map(lambda x:int(x.strip()), self.clashray_android_transports.split(",")))

        for proxy in raw_proxies:
            clash_proxy = {}
            if isinstance(proxy, dict):
                if proxy.get("is_ss", False) == True:
                    clash_proxy["name"] = proxy["ps"]
                    clash_proxy["type"] = "ss"
                    clash_proxy["server"] = proxy["add"]
                    clash_proxy["port"] = int(proxy["port"])
                    clash_proxy["cipher"] = proxy["enc"]
                    clash_proxy["password"] = proxy["password"]
                    clash_proxy["udp"] = True
                    if "plugin" in proxy:
                        clash_proxy["plugin"] = proxy["plugin"]
                        clash_proxy["plugin-opts"] = proxy.get("plugin-opts", proxy.get("plugin_opts", {}))
                elif proxy.get("is_ssr", False) == True:
                    clash_proxy["name"] = proxy["ps"]
                    clash_proxy["type"] = "ssr"
                    clash_proxy["server"] = proxy["add"]
                    clash_proxy["port"] = int(proxy["port"])
                    clash_proxy["cipher"] = proxy["enc"]
                    clash_proxy["password"] = proxy["password"]
                    clash_proxy["obfs"] = proxy["obfs"]
                    clash_proxy["protocol"] = proxy["protocol"]
                    clash_proxy["udp"] = True
                    if "obfs-param" in proxy:
                        clash_proxy["obfs-param"] = proxy["obfs-param"]
                    if "protocol-param" in proxy:
                        clash_proxy["protocol-param"] = proxy["protocol-param"]
                elif proxy.get("is_http", False) == True:
                    clash_proxy["name"] = proxy["ps"]
                    clash_proxy["type"] = "http"
                    clash_proxy["server"] = proxy["add"]
                    clash_proxy["port"] = int(proxy["port"])
                elif proxy.get("is_socks5", False) == True:
                    clash_proxy["name"] = proxy["ps"]
                    clash_proxy["type"] = "socks5"
                    clash_proxy["server"] = proxy["add"]
                    clash_proxy["port"] = int(proxy["port"])
                    clash_proxy["udp"] = True
                else:
                    # vmess
                    # kcp not supported
                    if proxy['net'] == "kcp":
                        continue

                    clash_proxy["name"] = proxy["ps"]
                    clash_proxy["type"] = "vmess"
                    clash_proxy["server"] = proxy["add"]
                    clash_proxy["port"] = int(proxy["port"])
                    clash_proxy["uuid"] = str(proxy["id"])
                    clash_proxy["alterId"] = proxy["aid"]
                    clash_proxy["cipher"] = "auto"
                    clash_proxy["udp"] = True
                    clash_proxy["tls"] = proxy.get("tls", "") == "tls"
                    clash_proxy["skip-cert-verify"] = False
                    if proxy["net"] != "tcp":
                        clash_proxy["network"] = proxy['net']
                    elif proxy['type'] == "http":
                        clash_proxy["network"] = "http"
                        if "ws-path" in clash_proxy:
                            clash_proxy["network"] = "ws"
                    clash_proxy["ws-path"] = proxy.get('path', "")
                    clash_proxy["ws-headers"] = {"Host": proxy.get('host', "")}

                clash_result["proxies"].append(clash_proxy)

                for group in clash_result.get("proxy-groups", []):
                    if "name" not in group:
                        raise ValueError(f"\"name\" not in one of the proxy-groups")
                    if "Node Sel" in group["name"]:
                        group["proxies"].append(clash_proxy["name"])
                
        if self.is_clashray:
            zones = clash_result.get("clashray-net-zones", {})
            clashray_proxies = []
            clashray_domain_rules = []
            clashray_ip_cidr_rules = []
            for zname, z in zones.items():
                scope = "external-scope-proxy"

                if zname in self.in_none_scope:
                    continue
                if zname in self.in_lan_scope:
                    scope = "lan-scope-proxy"
                if zname in self.in_self_scope:
                    scope = "self-scope-proxy"

                zproxy = z[scope]
                zhosts = z["hosts"]

                clashray_proxies.append(copy.deepcopy(zproxy))
                for zhost in zhosts:
                    if zhost["type"] == "DOMAIN-SUFFIX":
                        clashray_domain_rules.append(zhost["type"] + "," + zhost["value"] + "," + zproxy["name"])
                    if zhost["type"] == "IP-CIDR":
                        clashray_ip_cidr_rules.append(zhost["type"] + "," + zhost["value"] + "," + zproxy["name"])

            proxy_insert_index = -1
            for i, p in enumerate(clash_result["proxies"]):
                if p["name"] == "INSERT-CLASHRAY-PROXIES-HERE":
                    proxy_insert_index = i
                    break

            if proxy_insert_index >= 0:
                clash_result["proxies"][(proxy_insert_index+1):(proxy_insert_index+1)] = clashray_proxies

            domain_rule_insert_index = -1
            ip_cidr_rule_insert_index = -1
            for i, r in enumerate(clash_result["rules"]):
                if "INSERT-CLASHRAY-DOMAIN-RULES-HERE" in r:
                    domain_rule_insert_index = i
                    break

            if domain_rule_insert_index >= 0:
                clash_result["rules"][(domain_rule_insert_index+1):(domain_rule_insert_index+1)] = clashray_domain_rules

            for i, r in enumerate(clash_result["rules"]):
                if "INSERT-CLASHRAY-IP-CIDR-RULES-HERE" in r:
                    ip_cidr_rule_insert_index = i
                    break

            if ip_cidr_rule_insert_index >= 0:
                clash_result["rules"][(ip_cidr_rule_insert_index+1):(ip_cidr_rule_insert_index+1)] = clashray_ip_cidr_rules

        return yaml.safe_dump(clash_result, allow_unicode=True)

    def make_ssr_uri(self, proxy):
        proxy_copy = copy.deepcopy(proxy)
        proxy_copy["password"] = self.urlsafe_base64_encode(proxy_copy["password"])
        proxy_copy["ps"] = self.urlsafe_base64_encode(proxy_copy["ps"].strip())
        proxy_copy["group"] = self.urlsafe_base64_encode(proxy_copy.get("group", "metasub by shunf4"))

        proxy_copy["protocol"] = proxy_copy.get("protocol", "origin")
        proxy_copy["obfs"] = proxy_copy.get("obfs", "plain")
        proxy_copy["obfsparam"] = self.urlsafe_base64_encode(proxy_copy.get("obfs-param", ""))
        proxy_copy["protoparam"] = self.urlsafe_base64_encode(proxy_copy.get("protocol-param", ""))

        uri = "ssr://"
        uri += self.urlsafe_base64_encode("%(add)s:%(port)s:%(protocol)s:%(enc)s:%(obfs)s:%(password)s/?obfsparam=%(obfsparam)s&protoparam=%(protoparam)s&remarks=%(ps)s&group=%(group)s" % proxy_copy)
        return uri

    def get_ssr_result(self, raw_proxies: List[Dict]):
        proxy_description = {
                "password": "xxx",
                "add": "1.1.1.1",
                "port": "0",
                "enc": "aes-256-gcm",
                "ps": "SSR Subscribe here",
            }

        all_uris = self.make_ssr_uri(proxy_description)
        all_uris += "\n"

        for proxy in raw_proxies:
            if isinstance(proxy, dict):
                if proxy.get("is_ss", False) == True:
                    all_uris += self.make_ssr_uri(proxy)
                    all_uris += "\n"
                elif proxy.get("is_ssr", False) == True:
                    all_uris += self.make_ssr_uri(proxy)
                    all_uris += "\n"
                elif proxy.get("is_http", False) == True:
                    # TODO
                    pass
                elif proxy.get("is_socks5", False) == True:
                    pass
            elif isinstance(proxy, str):
                all_uris += proxy
                all_uris += "\n"

        return self.base64_encode(all_uris.strip() + "\n").strip()

    def get_ss_v2ss_result(self, raw_proxies: List[Dict]):
        all_uris = ""
        for proxy in raw_proxies:
            if isinstance(proxy, dict):
                if proxy.get("is_ss", False) == True:
                    all_uris += "ss://"
                    if self.out_type != "v2sip":
                        all_uris += self.base64_encode("%(enc)s:%(password)s@%(add)s:%(port)s" % proxy)
                    else: # v2ss
                        all_uris += self.base64_encode("%(enc)s:%(password)s" % proxy)
                        all_uris += "@%(add)s:%(port)s" % proxy
                    all_uris += "#" + urllib.parse.quote(proxy['ps'])
                elif proxy.get("is_http", False) == True:
                    # TODO
                    pass
                elif proxy.get("is_socks5", False) == True:
                    pass
                elif proxy.get("is_ssr", False) == True:
                    pass
                else:
                    all_uris += "vmess://"
                    all_uris += self.base64_encode(json.dumps(proxy, ensure_ascii=False))
                all_uris += "\n"
            else:
                all_uris += str(proxy)
                all_uris += "\n"

        self.debug(f"ssr all_uris: {all_uris}")
        return self.base64_encode(all_uris.strip() + "\n").strip()

    async def worker(self):
        try:
            try:
                with contextlib.closing(open(config.STATE_PATH, mode="r", encoding="utf-8")) as sf:
                    self.debug(f"Reading state from", config.STATE_PATH)
                    self.state = json.loads(sf.read())
            except Exception as e:
                self.debug(f"Reading state error {e}, fallback to empty")
                self.state = {}

            await self.get_metasub()
            raw_proxies = await self.fetch_raw_proxies()
            

            if self.out_type == "json":
                self.body_writer.write(json.dumps(raw_proxies, indent=2, ensure_ascii=False))
            elif self.out_type == "clash":
                self.body_writer.write(self.get_clash_result(raw_proxies))
            elif self.out_type == "ssr":
                self.body_writer.write(self.get_ssr_result(raw_proxies))
            else:
                self.body_writer.write(self.get_ss_v2ss_result(raw_proxies))

            self.debug("Saving state")
            try:
                with contextlib.closing(open(config.STATE_PATH, mode="w", encoding="utf-8")) as sf:
                    self.debug(f"Writing state to", config.STATE_PATH)
                    sf.write(json.dumps(self.state))
            except Exception as e:
                self.debug(f"Writing state error {e}")

            self.body_writer.end()
            self.body_writer = None
        except Exception:
            # self.debug(f"Error occurred: {repr(ex)}")
            error_str = f"Error occurred: {traceback.format_exc()}"
            self.error(error_str)
            self.body_writer.end()


    def execute(self):
        self.body_writer = SubscriptionResponder.ResponseBodyIterable()
        asyncio.create_task(self.worker())

        async def response_generator_func():
            async for chunk in self.body_writer:
                yield chunk

        return response_generator_func(), 200, {
            "Content-Type": "text/plain; charset=utf-8"
        }
        
