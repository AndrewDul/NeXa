"""Piper thread topology + RTF under different core budgets.

Starts the Piper HTTP server (external venv), applies a taskset affinity to
its process tree, runs a fixed synthesis workload, reports RTF + CPU-cores
used. Restores nothing at OS level (only the child process's own affinity,
which dies with it).
"""
import asyncio, io, os, subprocess, sys, time, wave
sys.path.insert(0, "/home/devdul/Projects/NeXa_IkiGai/src")
from nexa.tts import PL_VOICE, PiperHttpServer

TEXT = ("Czarna dziura to obszar w przestrzeni, w ktorym grawitacja jest tak silna, "
        "ze nic nie moze uciec, nawet swiatlo, a jej granica to horyzont zdarzen. "
        "Powstaje, gdy bardzo masywna gwiazda konczy zycie i zapada sie pod wlasnym ciezarem.")
REPS = 6


def read_status(pid):
    try:
        st = open(f"/proc/{pid}/status").read()
    except Exception:
        return {}
    d = {}
    for l in st.splitlines():
        if l.startswith("Threads:"):
            d["threads"] = int(l.split()[1])
        elif l.startswith("Cpus_allowed_list:"):
            d["cpus"] = l.split(":", 1)[1].strip()
    return d


def proc_cpu_jiffies(pid):
    total = 0
    try:
        for t in os.listdir(f"/proc/{pid}/task"):
            f = open(f"/proc/{pid}/task/{t}/stat").read().split()
            total += int(f[13]) + int(f[14])
    except Exception:
        pass
    return total


def set_affinity_tree(pid, cores: str):
    subprocess.run(["taskset", "-a", "-pc", cores, str(pid)], capture_output=True)


async def bench(cores_desc: str, cores: str | None):
    s = PiperHttpServer()
    await s.start()
    await s.prewarm()
    pid = s._process.pid
    if cores is not None:
        set_affinity_tree(pid, cores)
        await asyncio.sleep(0.3)
    stt = read_status(pid)
    print(f"\n=== Piper affinity: {cores_desc}  (Cpus_allowed_list={stt.get('cpus')}) ===")

    peak_threads = stt.get("threads", 0)
    j0 = proc_cpu_jiffies(pid)
    per = []
    for i in range(REPS):
        r0 = time.monotonic()
        audio = await s.synthesize(TEXT, voice=PL_VOICE)
        dt = time.monotonic() - r0
        w = wave.open(io.BytesIO(audio))
        asec = w.getnframes() / w.getframerate()
        per.append((dt, asec))
        peak_threads = max(peak_threads, read_status(pid).get("threads", 0))
    j1 = proc_cpu_jiffies(pid)

    wall = sum(d for d, _ in per)
    aud = sum(a for _, a in per)
    cpu_s = (j1 - j0) / 100.0
    rtfs = [d / a for d, a in per]
    print(f"  reps={REPS}  wall_total={wall:.2f}s  audio_total={aud:.2f}s")
    print(f"  RTF: min={min(rtfs):.3f} mean={sum(rtfs)/len(rtfs):.3f} max={max(rtfs):.3f}")
    print(f"  peak process threads: {peak_threads}")
    print(f"  Piper CPU over burst: ~{cpu_s:.2f} cpu-seconds  => ~{cpu_s/wall:.2f} cores while synthesizing")
    print(f"  faster-than-realtime? {'YES' if max(rtfs) < 1.0 else 'NO'}  "
          f"(worst RTF {max(rtfs):.3f})")
    await s.stop()
    await asyncio.sleep(1.0)


async def main():
    await bench("CONTROL (0-3, all 4 cores)", None)
    await bench("1 core (taskset -c 3)", "3")
    await bench("2 cores (taskset -c 2,3)", "2,3")
    await bench("3 cores (taskset -c 1,2,3)", "1,2,3")


asyncio.run(main())
