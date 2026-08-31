# bench perf_ollama_SpeakLeash-bielik-4.5b-v3.0-instruct-Q8_0

- generated: 2026-08-31T22:45:40.060749+00:00
- backend: ollama
- model: SpeakLeash/bielik-4.5b-v3.0-instruct:Q8_0
- model_info: `{"family": "llama", "parameter_size": "4.8B", "quantization_level": "Q8_0", "context_length": 8192}`
- options: `{"temperature": 0.7, "top_p": 0.8, "top_k": 20, "num_predict": 200, "num_ctx": 4096}`
- summary: `{"gen_tps_mean": 2.01, "gen_tps_all": [1.92, 1.96, 2.15], "ttft_s_all": [23.207, 0.573, 0.535], "load_s_first": 12.921, "peak_temp_c": 68.3}`

## perf runs

- run 1: gen_tps=1.92 ttft=23.207s load=12.921s eval_count=200 temp=66.7C
- run 2: gen_tps=1.96 ttft=0.573s load=0.059s eval_count=200 temp=68.3C
- run 3: gen_tps=2.15 ttft=0.535s load=0.059s eval_count=200 temp=68.3C

### sample output (run 1)

```
Sleep plays a critical role in the formation and consolidation of memories, which are essential for learning and retaining information. During sleep, particularly during specific stages such as rapid eye movement (REM) sleep and deep non-REM sleep, the brain actively processes and strengthens new memories. This process involves several mechanisms:

1. **Summarization**: Sleep helps to summarize experiences by integrating different pieces of information into coherent memory structures, making them easier to recall later on. This is particularly important for complex tasks or know
```
