# Seasonal Contract Aggregator

A Streamlit app for tracking and auditing winter vehicle operating hours across a full season.

## Setup

### 1. Benchmark hours

Copy the example file and populate it with your contract values:

```bash
cp benchmarks.example.json benchmarks.json
```

Edit `benchmarks.json` with your route IDs and contracted benchmark hours.

### 2. Streamlit secrets

Create `.streamlit/secrets.toml` (never committed):

```toml
[github]
token  = "ghp_your_token_here"
repo   = "your-org/your-repo"
branch = "main"

[benchmarks]
"R1-1" = 500
"R1-2" = 460
# add all your routes here
```

The `[benchmarks]` block takes precedence over `benchmarks.json` — use it for Streamlit Cloud deployments so benchmark values never enter the repo.

### 3. Run locally

```bash
pip install -r requirements.txt
streamlit run seasonal_aggregator.py
```

### Streamlit Cloud

Connect this repo in [share.streamlit.io](https://share.streamlit.io), set the main file to `seasonal_aggregator.py`, and paste your secrets into **Settings → Secrets**.
