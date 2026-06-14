# Intelligent LLM Gateway — common operations.
.PHONY: help install dev test lint serve demo traffic docker-build docker-run clean

help:           ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:        ## create venv + install deps
	python3 -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -r requirements.txt pytest

test:           ## run the test suite
	.venv/bin/python -m pytest -q

serve:          ## run the gateway + dashboard on :8000
	.venv/bin/python -m uvicorn gateway.app:app --host 0.0.0.0 --port 8000

dev:            ## run with autodemo traffic (self-driving demo)
	GATEWAY_AUTODEMO=1 .venv/bin/python -m uvicorn gateway.app:app --host 0.0.0.0 --port 8000

traffic:        ## stream realistic traffic at a running server
	.venv/bin/python -m scripts.traffic --n 400 --rps 12 --outage

docker-build:   ## build the production container
	docker build -t llm-gateway .

docker-run:     ## run the container (simulated + autodemo)
	docker run --rm -p 8000:8000 llm-gateway

clean:          ## remove caches
	find . -type d -name __pycache__ -prune -exec rm -rf {} + ; rm -rf .pytest_cache
