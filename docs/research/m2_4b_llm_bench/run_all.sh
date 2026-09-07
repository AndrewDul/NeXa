#!/usr/bin/env bash
# M2.4B.3.4 — chained benchmark batches. Sequential; each writes its own
# JSON. Run from repo root: bash docs/research/m2_4b_llm_bench/run_all.sh
set -u
cd /home/devdul/Projects/NeXa_IkiGai
PY=.venv/bin/python
B=docs/research/m2_4b_llm_bench
BIN="$PY $B/bench_llm.py"
D=20260907

echo "===== BATCH B: gemma4:e2b core+science+cold ====="
$BIN --models gemma4:e2b --set core,science --cold --out $B/bench_e2b_$D.json

echo "===== BATCH C: gemma4:e4b serving sweep ====="
$BIN --models gemma4:e4b --set serving --reps 2 --out $B/bench_serving_e4b_$D.json

echo "===== BATCH D: qwen3:4b-instruct + qwen3.5:4b core+science+cold ====="
$BIN --models qwen3:4b-instruct,qwen3.5:4b --set core,science --cold --out $B/bench_alt_$D.json

echo "===== BATCH E: fixed-length throughput, 7 models ====="
$BIN --models gemma4:e4b,gemma4:e2b,qwen3:4b-instruct,qwen3.5:4b,SpeakLeash/bielik-4.5b-v3.0-instruct:Q8_0,bielik-4.5b-v3-q4km-thirdparty:latest,llama3.2:latest,qwen2.5:3b \
     --set fixed --reps 3 --fixed-num-predict 170 --out $B/bench_fixed_$D.json

echo "===== BATCH F: Bielik science-truth (PL reference) ====="
$BIN --models SpeakLeash/bielik-4.5b-v3.0-instruct:Q8_0,bielik-4.5b-v3-q4km-thirdparty:latest \
     --set science --out $B/bench_bielik_science_$D.json

echo "===== ALL BATCHES DONE ====="
