# bench perf_ollama_llama3.2-latest

- generated: 2026-08-31T22:09:03.163829+00:00
- backend: ollama
- model: llama3.2:latest
- model_info: `{"family": "llama", "parameter_size": "3.2B", "quantization_level": "Q4_K_M", "context_length": 131072}`
- options: `{"temperature": 0.7, "top_p": 0.8, "top_k": 20, "num_predict": 200, "num_ctx": 4096}`
- summary: `{"gen_tps_mean": 4.35, "gen_tps_all": [4.44, 4.37, 4.23], "ttft_s_all": [13.065, 0.68, 0.684], "load_s_first": 11.203, "peak_temp_c": 68.3}`

## perf runs

- run 1: gen_tps=4.44 ttft=13.065s load=11.203s eval_count=191 temp=66.7C
- run 2: gen_tps=4.37 ttft=0.68s load=0.46s eval_count=200 temp=67.2C
- run 3: gen_tps=4.23 ttft=0.684s load=0.464s eval_count=200 temp=68.3C

### sample output (run 1)

```
Sleep plays a crucial role in memory consolidation, which is the process of transferring short-term memories to long-term ones. During sleep, our brains undergo various stages that help strengthen and refine memories. Research has shown that sleep helps to clear out unnecessary information, eliminate distractions, and enhance pattern recognition - all essential processes for solidifying learning and experiences.

When we don't get enough sleep or have poor quality sleep, it can lead to impaired memory function, difficulty concentrating, and reduced ability to learn new information. On the other hand, adequate sleep has been linked to improved memory performance, better recall, and enhanced cognitive flexibility.

Here are two practical tips to improve your sleep for better memory:

1. Establish a consistent sleep schedule: Go to bed and wake up at the same time every day, including weekends.
2. Create a sleep-conducive environment: Make your bedroom dark, quiet, and cool, and avoid stimulating activities before bedtime to promote relaxation.
```
