"""Bounded loopback readiness after activation; never contacts the school."""
import json
import re
import time
import uuid
import urllib.request

class RuntimeCheckError(ValueError):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise RuntimeCheckError("runtime_redirect_rejected")

ENDPOINTS=(
    (8791,"/_elevhub/health","elevhub-lokal","projekter"),
    (8792,"/_elevhub/health","elevhub-lokal","scratch"),
    (8797,"/_elevhub/health","elevhub-lokal","microbit"),
    (8795,"/api/runtime/health","elevhub-design",None),
    (8796,"/api/runtime/health","elevhub-design",None),
)

def read_health(url):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open(url,timeout=2) as response:
        if response.status!=200:raise RuntimeCheckError("runtime_http_status")
        raw=response.read(16385)
    if not 0<len(raw)<=16384:raise RuntimeCheckError("runtime_health_size")
    return json.loads(raw)

def check_once(core_version,call,read=read_health):
    if not isinstance(core_version,str) or re.fullmatch(r"[0-9][A-Za-z0-9.+:~\-]{0,127}",core_version) is None:
        raise RuntimeCheckError("runtime_core_version_required")
    pids={}
    for service in ("elevhub-lokal","elevhub-design","getty@tty1"):
        raw=call(["/usr/bin/systemctl","show","--property=ActiveState,MainPID",service+".service"])
        values=dict(line.split("=",1) for line in raw.splitlines() if "=" in line)
        if values.get("ActiveState")!="active" or not values.get("MainPID","").isdigit() or int(values["MainPID"])<=0:
            raise RuntimeCheckError("runtime_service_inactive")
        pids[service]=int(values["MainPID"])
    instances={}
    for port,path,service,app in ENDPOINTS:
        value=read("http://127.0.0.1:"+str(port)+path)
        if (not isinstance(value,dict) or value.get("release_id")!=core_version
                or value.get("service")!=service or type(value.get("pid")) is not int
                or value["pid"]!=pids[service] or value.get("local") is not True
                or (app is not None and value.get("app")!=app)):
            raise RuntimeCheckError("runtime_identity_mismatch")
        try:instance=str(uuid.UUID(value.get("runtime_instance")))
        except (ValueError,TypeError,AttributeError):raise RuntimeCheckError("runtime_instance_invalid") from None
        if service in instances and instances[service]!=instance:
            raise RuntimeCheckError("runtime_instance_mismatch")
        instances[service]=instance
    return {"core_version":core_version,"services":instances}

def wait_ready(core_version,call,*,read=read_health,timeout=30,clock=time.monotonic,sleep=time.sleep):
    deadline=clock()+timeout
    while True:
        try:return check_once(core_version,call,read)
        except (RuntimeCheckError,OSError,ValueError) as error:
            if clock()>=deadline:
                raise RuntimeCheckError(str(error) if isinstance(error,RuntimeCheckError) else "runtime_endpoint_unavailable") from error
            sleep(min(1,max(0,deadline-clock())))
