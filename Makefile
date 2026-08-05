.PHONY: install install-full diagnose all eval test api ui docker clean

install:        ## nothing to install — stdlib only
	@echo "No dependencies required. Run 'make all' to diagnose the sample incidents."

install-full:   ## langgraph, mlflow, fastapi, streamlit
	pip install -r requirements-full.txt

diagnose:       ## make diagnose I=data/incidents/002-skew-oom
	python scripts/diagnose.py $(I)

all:            ## diagnose every sample incident
	python scripts/diagnose.py --all

report:         ## make report I=data/incidents/002-skew-oom
	python scripts/diagnose.py $(I) --markdown

eval:           ## score classification against the labelled fixtures
	python scripts/run_eval.py

test:
	python -m pytest tests -q

api:
	uvicorn rca_agent.api:app --app-dir src --reload --port 8000

ui:
	streamlit run app/streamlit_app.py

docker:
	docker build -t pipeline-rca-agent .

clean:
	rm -rf runs.jsonl reports .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
