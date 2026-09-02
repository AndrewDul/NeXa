# bench perf_ollama_qwen3-4b-instruct

- generated: 2026-09-01T17:49:37.743445+00:00
- backend: ollama
- model: qwen3:4b-instruct
- model_info: `{"family": "qwen3", "parameter_size": "4.0B", "quantization_level": "Q4_K_M", "context_length": 262144}`
- options: `{"num_predict": 200, "num_ctx": 4096}`
- summary: `{"gen_tps_mean": 2.96, "gen_tps_all": [2.99, 2.85, 3.03], "ttft_s_all": [15.59, 0.391, 0.357], "load_s_first": 13.427, "peak_temp_c": 67.2}`

## perf runs

- run 1: gen_tps=2.99 ttft=15.59s load=13.427s eval_count=152 temp=64.5C
- run 2: gen_tps=2.85 ttft=0.391s load=0.001s eval_count=133 temp=66.7C
- run 3: gen_tps=3.03 ttft=0.357s load=0.001s eval_count=156 temp=67.2C

### sample output (run 1)

```
Sleep matters for memory because during sleep, especially during deep and REM stages, the brain processes and consolidates information from the day. It sorts out what’s important and stores it in long-term memory. Without enough sleep, the brain can’t properly transfer new memories from short-term storage to long-term storage, making it harder to learn and recall things.

Two practical tips:  
1. Aim for 7–9 hours of sleep each night, as consistent, quality sleep supports memory formation.  
2. Try to go to bed and wake up at the same time every day to regulate your body’s internal clock, which helps improve sleep quality and memory function.  

These habits support your brain’s ability to learn, remember, and stay sharp over time.
```
