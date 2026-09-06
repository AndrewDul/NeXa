"""M2.4B.1A — FAST CPU strategy benchmark.

Per strategy: apply affinity/nice (sudo for llama-server), then for ~35 s run
a concurrent LLM-generation loop + Piper-synthesis loop, measuring LLM
tok/s + prompt-eval, true Piper HTTP RTF, per-core CPU. Restore after.
Short num_predict + a hard timeout so a contended case can't hang.
"""
from __future__ import annotations
import asyncio, io, json, subprocess, sys, time, urllib.request, wave
sys.path.insert(0, "/home/devdul/Projects/NeXa_IkiGai/src")
from nexa.tts import PL_VOICE, PiperHttpServer

PIPER_TEXT = ("Czarna dziura to obszar w przestrzeni, w ktorym grawitacja jest tak silna, "
              "ze nic nie ucieka, a jej granica to horyzont zdarzen.")
LLM_MSG = "Jednym zdaniem: czym jest czarna dziura?"
WINDOW_S = 35.0


def sh(*c):
    return subprocess.run(c, capture_output=True, text=True).stdout.strip()


def llama_pids():
    return sh("pgrep", "-x", "llama-server").split()


def set_aff(pid, cores, sudo=False):
    subprocess.run((["sudo", "-n"] if sudo else []) + ["taskset", "-a", "-pc", cores, pid],
                   capture_output=True)


def set_nice(pid, n, sudo=False):
    subprocess.run((["sudo", "-n"] if sudo else []) + ["renice", "-n", str(n), "-p", pid],
                   capture_output=True)


def percpu():
    out = {}
    for l in open("/proc/stat"):
        if l.startswith("cpu") and len(l) > 3 and l[3].isdigit():
            p = l.split()
            out[p[0]] = (sum(int(x) for x in p[1:]), int(p[4]) + int(p[5]))
    return out


def llm_once():
    body = json.dumps({"model": "gemma4:e4b", "messages": [{"role": "user", "content": LLM_MSG}],
                       "stream": True, "options": {"temperature": 0.7, "num_predict": 24}}).encode()
    req = urllib.request.Request("http://localhost:11434/api/chat", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.monotonic(); first = None; meta = {}
    with urllib.request.urlopen(req, timeout=90) as r:
        for line in r:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("message", {}).get("content") and first is None:
                first = time.monotonic() - t0
            if d.get("done"):
                meta = dict(first_s=first, prompt_eval_s=d.get("prompt_eval_duration", 0) / 1e9,
                            ec=d.get("eval_count"), es=d.get("eval_duration", 0) / 1e9)
                break
    if meta.get("ec") and meta.get("es"):
        meta["tok_s"] = meta["ec"] / meta["es"]
    return meta


async def piper_loop(server, stop, calls):
    while not stop.is_set():
        r0 = time.monotonic()
        try:
            audio = await server.synthesize(PIPER_TEXT, voice=PL_VOICE)
        except Exception:
            await asyncio.sleep(0.3); continue
        dt = time.monotonic() - r0
        w = wave.open(io.BytesIO(audio)); asec = w.getnframes() / w.getframerate()
        calls.append((dt, asec, dt / asec))


async def llm_loop(stop, res):
    while not stop.is_set():
        try:
            res.append(await asyncio.wait_for(asyncio.to_thread(llm_once), timeout=90))
        except Exception as e:
            res.append({"err": str(e)[:60]})
        await asyncio.sleep(0.3)


async def case(name, *, lcores=None, lnice=None, pcores=None, pnice=None):
    print(f"\n=== {name} ===")
    server = PiperHttpServer(); await server.start(); await server.prewarm()
    ppid = str(server._process.pid)
    lps = llama_pids()
    try:
        for lp in lps:
            if lcores:
                set_aff(lp, lcores, sudo=True)
            if lnice is not None:
                set_nice(lp, lnice, sudo=True)
        if pcores:
            set_aff(ppid, pcores)
        if pnice is not None:
            set_nice(ppid, pnice)
        await asyncio.sleep(0.5)
        lp0 = lps[0] if lps else "?"
        print(f"  llama[{lp0}] {sh('bash','-c',f'grep -o \"0-3\\|0-2\\|0-1\\|[0-9]-[0-9]\\|[0-9]\" /proc/{lp0}/status | tr \"\\n\" \",\" 2>/dev/null') if lps else ''} "
              f"cpus={open(f'/proc/{lp0}/status').read().split('Cpus_allowed_list:')[1].split(chr(10))[0].strip() if lps else '?'} "
              f"nice={sh('ps','-o','nice=','-p',lp0)}")
        print(f"  piper[{ppid}] cpus={open(f'/proc/{ppid}/status').read().split('Cpus_allowed_list:')[1].split(chr(10))[0].strip()} "
              f"nice={sh('ps','-o','nice=','-p',ppid)}")

        stop = asyncio.Event()
        pc, lr = [], []
        c0 = percpu(); t0 = time.monotonic()
        pt = asyncio.create_task(piper_loop(server, stop, pc))
        lt = asyncio.create_task(llm_loop(stop, lr))
        await asyncio.sleep(WINDOW_S)
        stop.set()
        await asyncio.gather(pt, return_exceptions=True)
        await asyncio.wait_for(asyncio.gather(lt, return_exceptions=True), timeout=95)
        c1 = percpu(); wall = time.monotonic() - t0

        ok = [r for r in lr if "tok_s" in r]
        fts = [r["first_s"] for r in ok if r.get("first_s")]
        pes = [r["prompt_eval_s"] for r in ok if r.get("prompt_eval_s")]
        tks = [r["tok_s"] for r in ok]
        errs = [r for r in lr if "err" in r]
        prtf = [c[2] for c in pc]
        print(f"  LLM  n={len(ok)} err={len(errs)}  first_token={_m(fts)}s  "
              f"prompt_eval={_m(pes)}s  tok/s={_m(tks)} (min {_mn(tks)})")
        print(f"  Piper n={len(pc)}  true RTF mean={_m(prtf)} max={_mx(prtf)}  "
              f"audio/wall = {sum(c[1] for c in pc)/wall:.2f}")
        pcs = []
        for k in sorted(c0):
            dt = c1[k][0] - c0[k][0]; di = c1[k][1] - c0[k][1]
            pcs.append(f"{k}={100*(dt-di)/dt:.0f}%" if dt else f"{k}=?")
        print("  per-core:", " ".join(pcs),
              f" temp={int(sh('bash','-c','cat /sys/class/thermal/thermal_zone0/temp'))/1000:.0f}C",
              sh("vcgencmd", "get_throttled"))
    finally:
        for lp in llama_pids():
            set_aff(lp, "0-3", sudo=True); set_nice(lp, 0, sudo=True)
        try:
            await server.stop()
        except Exception:
            pass
        await asyncio.sleep(1.0)


def _m(x):
    return round(sum(x) / len(x), 2) if x else None
def _mn(x):
    return round(min(x), 2) if x else None
def _mx(x):
    return round(max(x), 3) if x else None


async def main():
    await case("CONTROL")
    await case("A: llama 0,1,2 / Piper 3", lcores="0,1,2", pcores="3")
    await case("B: llama 0,1 / Piper 2,3", lcores="0,1", pcores="2,3")
    await case("C: Piper nice +10 (no affinity)", pnice=10)
    await case("D: llama 0,1,2 / Piper 3 + nice +5", lcores="0,1,2", pcores="3", pnice=5)
    for lp in llama_pids():
        set_aff(lp, "0-3", sudo=True); set_nice(lp, 0, sudo=True)
    print("\nDONE. affinity/nice restored.")


asyncio.run(main())
