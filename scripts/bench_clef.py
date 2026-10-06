"""Latency/VRAM probe for a running llama-server with Clef-flash.

    uv run --with httpx scripts/bench_clef.py [--url http://127.0.0.1:8090] [-n 20]
"""
import argparse, statistics, subprocess, time
import httpx

NEWS_Q = {
    "category": {"type": "choice", "instructions": "Which topic is this headline about?",
                 "criteria": {"stocks": "stock market, earnings, macro, tickers",
                              "ai": "AI models, labs, research, AI products",
                              "startups": "startup funding, launches, founders",
                              "irrelevant": None}},
    "relevance": {"type": "score", "instructions": "How relevant is this to a CS student who follows AI, startups and markets?",
                  "criteria": ["not relevant", "slightly", "relevant", "very relevant", "must read"]},
    "breaking": {"type": "noul", "instructions": "Is this breaking news that just happened?"},
}
MAIL_Q = {
    "triage": {"type": "choice", "instructions": "Should the recipient read this email today?",
               "criteria": {"read_today": "needs attention or action today",
                            "fyi": "useful but can wait", "ignore": "promo, newsletter, noise"}},
}
SAMPLES = [
    ("news", "Nvidia shares jump 6% after earnings beat; data center revenue up 80% year over year", NEWS_Q),
    ("news", "Anthropic releases new Claude model with improved coding benchmarks", NEWS_Q),
    ("news", "YC-backed startup raises $40M Series A to build AI agents for accounting", NEWS_Q),
    ("news", "Local bakery wins award for best croissant in Atlanta", NEWS_Q),
    ("mail", {"from": "registrar@gatech.edu", "subject": "Action required: Phase II registration closes tonight",
              "snippet": "Your registration time ticket expires at 11:59pm today..."}, MAIL_Q),
    ("mail", {"from": "deals@doordash.com", "subject": "50% off your next 3 orders!", "snippet": "Treat yourself..."}, MAIL_Q),
]

def vram():
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout.strip()
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8090")
    ap.add_argument("-n", type=int, default=20)
    a = ap.parse_args()
    c = httpx.Client(base_url=a.url, timeout=60)
    c.post("/v1/systemone", json={"state": "warmup", "questions": MAIL_Q}).raise_for_status()
    lat, toks = [], []
    for i in range(a.n):
        kind, state, qs = SAMPLES[i % len(SAMPLES)]
        t = time.perf_counter()
        r = c.post("/v1/systemone", json={"state": state, "questions": qs}); r.raise_for_status()
        lat.append((time.perf_counter() - t) * 1000)
        j = r.json(); toks.append(j["usage"]["input_tokens"])
        if i < len(SAMPLES):
            ans = {k: v.get("choice", v.get("score", v.get("noul"))) for k, v in j["answers"].items()}
            conf = {k: round(v.get("confidence", v.get("noul", 0)), 3) for k, v in j["answers"].items()}
            print(f"{kind:4s} {str(state)[:60]:60s} {ans} {conf}")
    lat.sort()
    print(f"\nreqs={a.n} tokens/req={statistics.mean(toks):.0f}  p50={lat[len(lat)//2]:.0f}ms  "
          f"p95={lat[int(len(lat)*.95)-1]:.0f}ms  max={lat[-1]:.0f}ms  ~{60000/statistics.mean(lat):.0f} decisions/min")
    print("VRAM used,total MiB:", vram())

if __name__ == "__main__":
    main()
