# bench perf_ollama_phi4-mini-3.8b

- generated: 2026-09-01T18:28:52.368856+00:00
- backend: ollama
- model: phi4-mini:3.8b
- model_info: `{"family": "phi3", "parameter_size": "3.8B", "quantization_level": "Q4_K_M", "context_length": 131072}`
- options: `{"num_predict": 200, "num_ctx": 4096}`
- summary: `{"gen_tps_mean": 4.12, "gen_tps_all": [4.1, 4.11, 4.14], "ttft_s_all": [13.961, 0.274, 0.263], "load_s_first": 12.679, "peak_temp_c": 69.4}`

## perf runs

- run 1: gen_tps=4.1 ttft=13.961s load=12.679s eval_count=167 temp=68.3C
- run 2: gen_tps=4.11 ttft=0.274s load=0.005s eval_count=159 temp=69.4C
- run 3: gen_tps=4.14 ttft=0.263s load=0.002s eval_count=172 temp=68.8C

### sample output (run 1)

```
Sleep plays a crucial role in memory consolidation, which is the process of stabilizing and storing memories. During sleep, particularly during the deep stages of non-REM (Rapid Eye Movement) sleep, the brain replays and organizes information acquired during the day, helping to transfer it from short-term to long-term storage. This process strengthens neural connections and enhances memory retention. Lack of sleep disrupts this delicate balance, leading to difficulties in learning and memory retention.

Practical Tips:
1. Establish a regular sleep routine by going to bed and waking up at the same time every day, even on weekends, to improve the quality and duration of sleep.
2. Create a sleep-conducive environment by keeping your bedroom cool, dark, and quiet, and limit exposure to screens before bedtime to enhance sleep quality and promote better memory consolidation.
```
