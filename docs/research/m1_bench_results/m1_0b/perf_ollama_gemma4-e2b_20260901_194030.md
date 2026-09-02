# bench perf_ollama_gemma4-e2b

- generated: 2026-09-01T18:40:30.054306+00:00
- backend: ollama
- model: gemma4:e2b
- model_info: `{"family": "gemma4", "parameter_size": "5.1B", "quantization_level": "Q4_K_M", "context_length": 131072}`
- options: `{"num_predict": 200, "num_ctx": 4096}`
- summary: `{"gen_tps_mean": 6.58, "gen_tps_all": [6.47, 6.64, 6.62], "ttft_s_all": [26.232, 0.317, 0.3], "load_s_first": 25.345, "peak_temp_c": 68.8}`

## perf runs

- run 1: gen_tps=6.47 ttft=26.232s load=25.345s eval_count=200 temp=67.2C
- run 2: gen_tps=6.64 ttft=0.317s load=0.011s eval_count=200 temp=68.8C
- run 3: gen_tps=6.62 ttft=0.3s load=0.001s eval_count=190 temp=68.8C

### sample output (run 1)

```
Sleep is absolutely crucial for memory because it's when our brains consolidate the information we've learned during the day. While we sleep, the brain actively processes new experiences, strengthening the neural connections associated with those memories. During different sleep cycles, the brain moves memories from short-term storage into long-term storage, effectively organizing and cementing them. Without adequate sleep, this consolidation process is disrupted, making it much harder to encode new information and retrieve old memories effectively. Essentially, sleep acts as the brain's essential "save and organize" time for learning.

**Two Practical Tips:**

1. **Establish a Consistent Schedule:** Try to go to bed and wake up around the same time every day, even on weekends, to regulate your body's natural sleep-wake cycle.
2. **Practice Good Sleep Hygiene:** Avoid screens (phones, TV) for at least an hour before bed, and ensure your bedroom is dark, cool, and quiet to promote deeper, more restorative
```
