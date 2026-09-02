# bench perf_ollama_bielik-4.5b-v3-q4km-thirdparty

- generated: 2026-09-02T17:08:20.816880+00:00
- backend: ollama
- model: bielik-4.5b-v3-q4km-thirdparty
- model_info: `{"family": "llama", "parameter_size": "4.8B", "quantization_level": "Q4_K_M", "context_length": 8192}`
- options: `{"num_predict": 200, "num_ctx": 4096}`
- summary: `{"gen_tps_mean": 3.77, "gen_tps_all": [3.76, 3.76, 3.78], "ttft_s_all": [17.183, 0.284, 0.283], "load_s_first": 12.091, "peak_temp_c": 68.3}`

## perf runs

- run 1: gen_tps=3.76 ttft=17.183s load=12.091s eval_count=200 temp=68.3C
- run 2: gen_tps=3.76 ttft=0.284s load=0.001s eval_count=200 temp=68.3C
- run 3: gen_tps=3.78 ttft=0.283s load=0.002s eval_count=200 temp=68.3C

### sample output (run 1)

```
Sleep is crucial for memory because it allows the brain to process and consolidate new information acquired during the day. While we sleep, the brain activates neural pathways and strengthens connections between neurons, which helps to create long-lasting memories. This process, known as consolidation, is essential for retaining information and retrieving it later.

Two practical tips for improving sleep and memory:

1. **Establish a Regular Sleep Routine**: Going to bed and waking up at the same time every day helps regulate your body's internal clock, promoting better sleep quality
```
