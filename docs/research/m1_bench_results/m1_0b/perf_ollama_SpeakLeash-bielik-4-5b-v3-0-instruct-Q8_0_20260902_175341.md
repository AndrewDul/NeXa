# bench perf_ollama_SpeakLeash-bielik-4.5b-v3.0-instruct-Q8_0

- generated: 2026-09-02T16:53:41.081331+00:00
- backend: ollama
- model: SpeakLeash/bielik-4.5b-v3.0-instruct:Q8_0
- model_info: `{"family": "llama", "parameter_size": "4.8B", "quantization_level": "Q8_0", "context_length": 8192}`
- options: `{"num_predict": 200, "num_ctx": 4096}`
- summary: `{"gen_tps_mean": 2.04, "gen_tps_all": [2.03, 2.05, 2.05], "ttft_s_all": [31.276, 0.563, 0.52], "load_s_first": 21.141, "peak_temp_c": 67.8}`

## perf runs

- run 1: gen_tps=2.03 ttft=31.276s load=21.141s eval_count=200 temp=67.8C
- run 2: gen_tps=2.05 ttft=0.563s load=0.001s eval_count=200 temp=67.8C
- run 3: gen_tps=2.05 ttft=0.52s load=0.003s eval_count=200 temp=67.2C

### sample output (run 1)

```
Sleep is crucial for memory because it allows the brain to process and consolidate information acquired during the day. When you sleep, your brain activates the neural pathways that store memories, strengthening them and making them more resistant to forgetting. This process, known as consolidation, is essential for the formation of long-term memories. During sleep, the brain also eliminates waste products and repairs damage, which further supports memory retention.

Two practical tips for improving sleep and memory are:
1. **Establish a consistent sleep schedule**: Going
```
