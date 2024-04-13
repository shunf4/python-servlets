from quart import request, Response
from register import GLOBAL_REGISTER
from subprocess import PIPE, Popen, STDOUT

async def handle():
    p = Popen("dd if=/dev/zero of=/tmp/random.file bs=4096 count=2560", shell=True, stdout=PIPE, stderr=STDOUT)
    stdout, _ = p.communicate()
    return Response(stdout, mimetype='text/plain')

