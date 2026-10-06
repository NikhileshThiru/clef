#!/bin/bash
# Start llama-server in the foreground with the measured config (extra args appended).
exec ~/.local/src/llama.cpp/build/bin/llama-server \
  -m ~/.local/share/clef/models/Clef-Flash-Q4_K_M.gguf \
  --host 127.0.0.1 --port 8090 -ngl 99 -c 4096 -b 2048 -ub 2048 -ot "output\.weight=CPU" "$@"
