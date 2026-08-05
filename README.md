# Pipeline RCA Agent

An agent that reads Airflow task metadata and Spark logs when a job fails,
works out *why*, and writes the incident summary — so the person who gets
paged at 3am starts from a diagnosis instead of a stack trace.

```
$ make all

======================================================================
txn_aml_features.build_counterparty_features   [002-skew-oom]
======================================================================
  class      : data_skew  (84%)
  severity   : SEV1
  signals    : shuffle_spill, skew_partition, straggler
  rationale  : OOM/executor loss appears alongside skew markers — treating as
               data skew rather than under-provisioning; adding memory would mask it.

  Work is unevenly distributed across partitions; a small number of tasks
  carry most of the data.

  Next steps:
    1. Identify the hot key from the partition row counts in the stage metrics.
    2. Apply salting to the join key, or enable AQE skew join handling.
    3. Consider broadcasting the smaller side if it fits.

  Closest past incident: INC-2026-0388 (0.3444) — Salted the join key with a
  16-way split and enabled AQE skew join handling. Runtime fell from 68 min to 9.
```

That log contains `OutOfMemoryError` and `Container killed by YARN`. A naive
classifier calls that a resource problem and someone provisions a bigger
cluster — which costs money every night and does not fix it. This one calls it
skew, because the OOM arrives alongside a straggler task and an 18-million-row
partition.

---

## Running it

```bash
git clone <this repo> && cd pipeline-rca-agent
make all          # diagnose all five sample incidents
make eval         # score classification against the labels
make test         # 21 tests
```

**No dependencies.** Not "minimal dependencies" — the core is standard library
only. No pip install, no API key, no model download.

Optional upgrades, each detected automatically when installed:

| Install | Replaces |
|---|---|
| `langgraph` | the built-in `SimpleGraph` executor with a compiled state machine |
| `openai` / `anthropic` key | template narration with generated prose |
| `mlflow` | the JSONL run log with MLflow tracking |
| `fastapi` + `streamlit` | CLI-only with a service and a UI |

`make install-full` gets all of them.

---

## How it works

```
                    ┌──────────┐
   Airflow API ────▶│ collect  │
   Spark logs  ────▶└────┬─────┘
                         ▼
                   ┌──────────┐
                   │ classify │  weighted signal voting + corroboration rules
                   └────┬─────┘
                        ▼
                   ┌──────────┐
                   │ retrieve │  similar past incidents, runbook for this class
                   └────┬─────┘
                        │
          confidence < threshold ──▶ ┌──────────┐
                        │            │ escalate │
                        ▼            └────┬─────┘
                   ┌──────────┐◀──────────┘
                   │ diagnose │  severity, remediation, narrative
                   └────┬─────┘
                        ▼
              Diagnosis(class, severity, root_cause, remediation[], evidence[])
```

### The graph is deterministic on purpose

The nodes run in a fixed order. There is no model deciding what to do next.

That is a deliberate choice, not a limitation. An on-call engineer needs the
same failure to produce the same diagnosis twice — a report that varies run to
run is worse than no report, because you cannot tell whether the pipeline
changed or the model did. There are five steps in an obvious order; handing
that routing to an LLM would add latency, cost and nondeterminism to buy
nothing.

The one conditional edge is `confidence < MIN_CONFIDENCE → escalate`, and low
confidence is treated as a *result* rather than a failure. The agent says the
signals were ambiguous instead of emitting a confident-sounding guess someone
will spend an hour chasing.

### Why there are two graph runtimes

`RCAAgent` compiles a LangGraph `StateGraph` when LangGraph is installed, and
falls back to `SimpleGraph` — thirty lines — when it is not.

This is not a shortcut. Every node is a pure `state -> state` function with no
framework types in its signature, which is what makes the classification logic
unit-testable without spinning up a graph runtime. Building framework-first
would have meant every test of "does OOM plus straggler classify as skew"
required LangGraph installed and a compiled graph to assert against. The
fallback fell out of that design rather than motivating it.

### Classification is rules, not an LLM

Weighted voting over regex signals extracted from the log, with corroboration
rules layered on top. No model involved.

Log signals are regular enough that rules win here, and rules are free,
instant, deterministic and explainable — you can print the score breakdown and
see exactly why it decided what it decided. The LLM's job starts *after* the
diagnosis: writing three sentences of prose. Everything that matters is
already fixed by then.

Signal weights reflect **specificity, not severity**:

| Signal | Class | Weight | Why |
|---|---|---|---|
| `cannot resolve 'x' given input columns` | schema_drift | 3.0 | means one thing and nothing else |
| `AccessDenied` / `ExpiredToken` | permission | 3.0 | unambiguous |
| `Sensor has timed out` | source_delay | 3.0 | unambiguous |
| `row count variance` | data_quality | 3.0 | the gate said so explicitly |
| `OutOfMemoryError` | resource | 1.8 | a *symptom*; skew, under-provisioning and real growth all produce it |
| `ExecutorLostFailure` | resource | 1.8 | same |

Alarming-looking errors score *lower* when several different root causes
produce them. That inversion is the whole idea.

### The corroboration rules

Single signals lie. Three rules encode that:

**1. Skew wearing a resource costume.** OOM or executor loss *plus* any of
straggler / shuffle spill / hot partition → reclassify as `data_skew` and halve
the resource score. Adding memory to a skewed join moves the failure later in
the run and bills you for it nightly.

**2. Transient errors are only transient while retries remain.** A connection
reset on try 1 of 3 is noise. The same error on try 3 of 3 is a real failure
wearing a disguise, so its weight drops to 40%.

**3. A clean log on an `upstream_failed` task is evidence, not absence.** The
task never ran. That is a dependency failure, diagnosed at 0.9 confidence
without needing a single log line.

There is also a near-tie guard: if the runner-up scores within 15% of the
winner, confidence is scaled down and the report says so. A 51/49 split is not
an answer.

### Reporting the right task

Airflow marks every downstream task `upstream_failed` when something breaks.
`find_failed_task()` returns the earliest task in state `failed` — the one that
actually broke — not the collateral. Reporting the downstream task sends the
on-call engineer to a file that is working fine. There is a test for this.

---

## Evaluation

```bash
$ make eval
{
  "cases": 5,
  "accuracy": 1.0,
  "mean_confidence": 0.9674,
  "per_class": {
    "data_quality": 1.0,
    "data_skew": 1.0,
    "permission": 1.0,
    "schema_drift": 1.0,
    "source_delay": 1.0
  },
  "runtime": "simple",
  "narrator": "template"
}
```

Scored **per class**, not just in aggregate, because aggregate accuracy hides
the confusion that costs money: calling skew a resource problem is a
provisioning decision, not a rounding error.

**Read this number honestly.** Five labelled fixtures is enough to catch
regressions and demonstrate the corroboration logic. It is not enough to claim
general accuracy, and the fixtures were written alongside the signal patterns,
which is a real source of optimism. On live traffic the interesting number
would be the `unknown` rate — how often a production failure matches no known
signal — and that requires production logs to measure.

The tests pin the specific behaviours rather than the headline number:
`test_skew_beats_resource_when_corroborated`,
`test_transient_downweighted_when_retries_exhausted`,
`test_repeated_signal_does_not_dominate`,
`test_upstream_failed_with_clean_log_is_dependency`.

---

## Sample incidents

| Fixture | Failure | What makes it interesting |
|---|---|---|
| `001-schema-drift` | LIS vendor renamed a column mid-feed | downstream task is `upstream_failed`; must report the right one |
| `002-skew-oom` | skewed counterparty join | OOM present, correct answer is skew, SEV1 (regulatory) |
| `003-permission` | S3 grant revoked by entitlement review | unambiguous, tests the clean path |
| `004-source-delay` | sensor timed out, extract never landed | failure is in the sensor, not a Spark job |
| `005-data-quality` | publication gate blocked on 32% row variance | SEV1, and the gate working correctly *is* the incident |

Each is a directory containing `tasks.json` (Airflow REST API shape),
`spark.log`, and `meta.json`. Synthetic, but shaped from failures that actually
happen.

---

## Serving

```bash
make api     # FastAPI  → http://localhost:8000/docs
make ui      # Streamlit → http://localhost:8501
make report I=data/incidents/002-skew-oom   # full markdown incident report
```

`POST /diagnose` takes a DAG id, task id and raw log text — the live path.
`POST /diagnose/fixture/{name}` runs a bundled sample. `GET /health` reports
which graph runtime and narrator are active, because the system degrades
silently and you want that visible.

---

## Pointing it at a real deployment

The collectors read fixture files. Two swaps make it live:

1. **`collectors/airflow.py`** — replace `load_task_instances(path)` with a call
   to `GET /dags/{dag_id}/dagRuns/{run_id}/taskInstances`. The parser already
   consumes the REST API's JSON shape, so only the fetch changes.
2. **`collectors/spark.py`** — replace `tail_log(path)` with an S3 range read of
   the last N KB of the driver log. `tail_log` already truncates to the last
   lines because failures announce themselves at the end and executor logs run
   to gigabytes.

Then trigger it from an Airflow `on_failure_callback`.

---

## Layout

```
src/rca_agent/
  config.py            env-driven settings
  models.py            Incident, TaskInstance, LogEvidence, Diagnosis
  collectors/
    airflow.py         task metadata, originating-failure detection
    spark.py           weighted signal patterns
  classifier.py        voting + corroboration rules
  knowledge.py         TF-IDF over past incidents and runbooks
  graph.py             the state machine (LangGraph + fallback)
  report.py            severity, remediation, markdown rendering
  llm.py               OpenAI / Anthropic / template
  evaluation.py        per-class scoring
  tracking.py          MLflow with JSONL fallback
  api.py               FastAPI
app/                   Streamlit UI
data/incidents/        5 labelled fixtures + history.json
data/runbooks/         5 runbooks
tests/                 21 tests
```

## Known limitations

- **Signals are English regex.** A log in another language, or a custom
  exception with novel wording, classifies as `unknown`. That is the honest
  outcome, but it means coverage depends on curating patterns.
- **Five fixtures.** Written alongside the patterns they test, so the accuracy
  figure is optimistic. Real validation needs production logs.
- **No feedback loop.** When an engineer corrects a classification, nothing
  learns from it. The obvious next build is writing resolved incidents back to
  `history.json` and weighting retrieval by how often a past resolution worked.
- **Single-DAG scope.** It diagnoses one failed run. A cluster-wide incident
  affecting thirty DAGs produces thirty separate diagnoses instead of one
  correlated root cause.
- **Rules do not generalise.** A genuinely novel failure mode needs a human to
  add a pattern. That is the trade for determinism, and on balance the right
  one here — but it is a trade.
