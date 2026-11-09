SHELL := /bin/bash
-include .env
export

.PHONY: up down migrate ingest-dev ingest-dry index test test-fast test-go lint notebooks

up:              ## start Postgres + pgvector and apply migrations
	docker compose up -d --wait
	uv run signaldesk db migrate

down:
	docker compose down

migrate:
	uv run signaldesk db migrate

bin/ingest: ingest/go.mod ingest/cmd/ingest/main.go ingest/internal/edgar/*.go ingest/internal/store/*.go
	cd ingest && go build -o ../bin/ingest ./cmd/ingest

ingest-dry: bin/ingest   ## list what the dev slice would fetch (needs SEC_USER_AGENT)
	./bin/ingest -tickers-file eval/dev_slice_tickers.txt -forms 10-K:1,10-Q:2,8-K:2 -dry-run

ingest-dev: bin/ingest   ## fetch the ~50-document dev slice
	./bin/ingest -tickers-file eval/dev_slice_tickers.txt -forms 10-K:1,10-Q:2,8-K:2

index:           ## parse -> chunk (3 configs) -> embed (2 models)
	uv run signaldesk index

test: test-go    ## all tests
	uv run pytest -q

test-fast:       ## unit tests without model downloads
	uv run pytest -q -m "not models"

test-go:
	cd ingest && go vet ./... && go test ./...

lint:
	uv run ruff check src tests notebooks

notebooks:       ## execute notebooks; executed copies land in reports/
	uv run python scripts/run_notebooks.py
