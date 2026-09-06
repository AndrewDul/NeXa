import json, time, urllib.request


def run(label, prompt="Krotko wyjasnij czym jest czarna dziura i jak powstaje. Dwa zdania."):
    body = json.dumps({
        "model": "gemma4:e4b",
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "options": {"temperature": 0.7},
    }).encode()
    req = urllib.request.Request("http://localhost:11434/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.monotonic(); first = None; n = 0; last = t0; gaps = []; txt = ""
    with urllib.request.urlopen(req, timeout=120) as r:
        for line in r:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            c = d.get("message", {}).get("content", "")
            if c:
                now = time.monotonic()
                if first is None:
                    first = now - t0
                else:
                    gaps.append(now - last)
                last = now; n += 1; txt += c
            if d.get("done"):
                ev = d.get("eval_count"); ed = d.get("eval_duration", 0) / 1e9
                pd = d.get("prompt_eval_duration", 0) / 1e9
                lc = d.get("load_duration", 0) / 1e9
                print(f"  [{label}] server: load={lc:.2f}s prompt_eval={pd:.2f}s "
                      f"eval_count={ev} eval_dur={ed:.2f}s ({ev/ed if ed else 0:.2f} tok/s)")
                break
    tot = time.monotonic() - t0
    gaps.sort()
    p50 = gaps[len(gaps) // 2] if gaps else 0
    p95 = gaps[int(len(gaps) * 0.95)] if gaps else 0
    mx = gaps[-1] if gaps else 0
    print(f"  [{label}] client: first_token={first:.2f}s chunks={n} total={tot:.1f}s "
          f"chunk-gap p50={p50 * 1000:.0f}ms p95={p95 * 1000:.0f}ms max={mx * 1000:.0f}ms chars={len(txt)}")


if __name__ == "__main__":
    run("warm-1")
    run("warm-2")
    run("warm-3-longer", "Wyjasnij szczegolowo czym jest czarna dziura, jak powstaje, "
        "co to horyzont zdarzen i osobliwosc. Cztery, piec zdan.")
