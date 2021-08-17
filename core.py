from quart import Quart
from quart import Response
import os
import re
import sys
import asyncio
import traceback
import importlib
import importlib.util
import types
import config
from werkzeug.routing import Rule

app = Quart("python-servlets")

PROJECT_PATH = os.path.dirname(os.path.abspath(__file__))

def format_exception_with_trace(ex: Exception):
    return "\n".join(traceback.format_exception(type(ex), ex, ex.__traceback__))

@app.route("/")
async def main():
    return "Visit /f/<function name>, like /f/myip"

def validate_function_name(function_name: str) -> None:
    if re.fullmatch(r'^[.A-Za-z0-9_]+$', function_name) is None:
        raise ValueError(f"Function name should be ^[.A-Za-z0-9_]+$, got {function_name}")

app.url_map.add(Rule('/f/<string:function_name>', endpoint='f'))
@app.endpoint('f')
async def execute_function(function_name: str):
    function_name = function_name.replace(".", "_")
    validate_function_name(function_name)

    function_path = os.path.join(PROJECT_PATH, function_name)

    if not os.path.isdir(function_path):
        return f"Function not found: {function_name}", 404

    spec = importlib.util.spec_from_file_location(function_name, function_path + "/__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    modules_to_unload = [(k, v) for k, v in sys.modules.items()
        if (k == function_name or k.startswith(function_name + "."))
            and isinstance(v, types.ModuleType)]

    for k, v in modules_to_unload:
        del sys.modules[k]

    return await mod.handle()

@app.errorhandler(500)
def handle_exception(e: Exception):
    return f"Error occurred: {format_exception_with_trace(e)}", 500, {
            "Content-Type": "text/plain; charset=utf-8"
        }

app.run(host=config.HOST, port=config.PORT)
