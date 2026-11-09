import sys, asyncio, threading, time, subprocess
sys.path.insert(0, sys.argv[1])
import importlib.util, types
# load package modules without HA
pkg = types.ModuleType("hlc7"); pkg.__path__=[sys.argv[1]]; sys.modules["hlc7"]=pkg
import hlc7.streamer as st, hlc7.rtsp as rt, hlc7.xiaomi_p2p as p
data=open("/tmp/rt/test.h265","rb").read(); alaw=open("/tmp/rt/test.alaw","rb").read()
nals=st.split_nals(data)
aus=[];cur=[]
for n in nals:
    if (n[0]>>1)&0x3f==35:
        if cur: aus.append(cur); cur=[]
        continue
    cur.append(n)
if cur: aus.append(cur)
print("AUs",len(aus))
class Fake(st.CameraStreamer):
    def _connect(self):
        return types.SimpleNamespace(protocol="fake", remote="x", close=lambda: None)
    def _pump(self, client):
        t0=time.time(); i=0; ai=0
        while not self._stop.is_set():
            if self._should_close(): return
            au=aus[i%len(aus)]
            payload=b"".join(b"\x00\x00\x00\x01"+n for n in au)
            pk=p.MediaPacket(p.CODEC_H265,i,0,int(i*1000/15),payload)
            self._learn_params(pk)
            for c in list(self._consumers.values()): c(pk)
            for _ in range(2):
                a=alaw[(ai*320)%len(alaw):][:320]; ai+=1
                ap=p.MediaPacket(p.CODEC_PCMA,ai,0,0,a)
                for c in list(self._consumers.values()): c(ap)
            i+=1; time.sleep(max(0,t0+i/15-time.time()))
st.IDLE_CLOSE_S=2
s=Fake(None,threading.Lock(),"cn","1","x")
s._client=None
async def main():
    srv=rt.RtspServer(routes={"/camara_lavanderia": s}, host="0.0.0.0", port=18556, username="frigate", password="segredo123"); await srv.start()
    bad=await asyncio.create_subprocess_exec("ffprobe","-v","error","-rtsp_transport","tcp","rtsp://127.0.0.1:18556/camara_lavanderia",stderr=asyncio.subprocess.PIPE)
    _,e=await bad.communicate(); print("no-auth:", e.decode().strip()[-60:])
    srv_url="rtsp://frigate:segredo123@127.0.0.1:18556/camara_lavanderia"
    nf=await asyncio.create_subprocess_exec("ffprobe","-v","error","-rtsp_transport","tcp","rtsp://frigate:segredo123@127.0.0.1:18556/outra",stderr=asyncio.subprocess.PIPE)
    _,e=await nf.communicate(); print("wrong path:", e.decode().strip()[-40:])
    class _U: url=srv_url
    srv.url_override=srv_url
    proc=await asyncio.create_subprocess_exec("ffmpeg","-hide_banner","-loglevel","warning","-y","-rtsp_transport","tcp","-i",srv_url,"-t","3","-c:v","copy","-c:a","pcm_s16le","/tmp/rt/out.mkv")
    await proc.wait(); print("ffmpeg rc",proc.returncode)
    await asyncio.sleep(3); print("streamer running after idle:", s.running)
    await srv.stop()
asyncio.run(main())
