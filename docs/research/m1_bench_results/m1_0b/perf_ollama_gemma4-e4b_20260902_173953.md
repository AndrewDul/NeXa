# bench perf_ollama_gemma4-e4b

- generated: 2026-09-02T16:39:53.047923+00:00
- backend: ollama
- model: gemma4:e4b
- model_info: `{"family": "gemma4", "parameter_size": "8.0B", "quantization_level": "Q4_K_M", "context_length": 131072}`
- options: `{"num_predict": 200, "num_ctx": 4096}`
- summary: `{"gen_tps_mean": 3.11, "gen_tps_all": [3.14, 3.13, 3.06], "ttft_s_all": [34.644, 0.821, 0.622], "load_s_first": 32.4, "peak_temp_c": 66.7}`

## perf runs

- run 1: gen_tps=3.14 ttft=34.644s load=32.4s eval_count=163 temp=62.2C
- run 2: gen_tps=3.13 ttft=0.821s load=0.002s eval_count=199 temp=65.0C
- run 3: gen_tps=3.06 ttft=0.622s load=0.003s eval_count=170 temp=66.7C

### sample output (run 1)

```
Sleep is crucial for memory because it's when your brain processes and solidifies the information you've learned during the day. Think of it like decluttering a hard drive: while you're awake, new memories are created, but sleep allows your brain to review these 'raw files,' strengthening the important connections and pruning away the irrelevant noise. This process, called memory consolidation, moves fragile memories from short-term to long-term storage. Without adequate sleep, learning feels scattered, making recall difficult.

Two practical tips are:
1. **Review before bed:** Spend 10 minutes before sleep briefly recalling what you learned that day.
2. **Maintain a consistent schedule:** Going to bed and waking up around the same time, even on weekends, regulates your natural sleep cycles.
```
