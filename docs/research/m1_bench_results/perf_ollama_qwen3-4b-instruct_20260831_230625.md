# bench perf_ollama_qwen3-4b-instruct

- generated: 2026-08-31T22:06:25.470338+00:00
- backend: ollama
- model: qwen3:4b-instruct
- model_info: `{"family": "qwen3", "parameter_size": "4.0B", "quantization_level": "Q4_K_M", "context_length": 262144}`
- options: `{"temperature": 0.7, "top_p": 0.8, "top_k": 20, "num_predict": 200, "num_ctx": 4096}`
- summary: `{"gen_tps_mean": 3.89, "gen_tps_all": [4.03, 3.84, 3.79], "ttft_s_all": [9.191, 0.618, 0.631], "load_s_first": 7.605, "peak_temp_c": 66.7}`

## perf runs

- run 1: gen_tps=4.03 ttft=9.191s load=7.605s eval_count=158 temp=62.8C
- run 2: gen_tps=3.84 ttft=0.618s load=0.366s eval_count=152 temp=65.0C
- run 3: gen_tps=3.79 ttft=0.631s load=0.375s eval_count=143 temp=66.7C

### sample output (run 1)

```
Sleep matters for memory because during sleep, especially during deep sleep and REM stages, your brain processes and consolidates information from the day. It sorts out what's important, strengthens neural connections, and moves memories from short-term to long-term storage. Without enough sleep, the brain can't properly organize these memories, leading to confusion, poor learning, and forgetfulness.

Two practical tips:  
1. Aim for 7–9 hours of sleep each night consistently—this helps your brain get the time it needs to process memories.  
2. Try to go to bed and wake up at the same time every day to regulate your body's internal clock, which supports better sleep quality and memory function.  

These habits support a healthy brain and improve how well you remember things over time.
```
