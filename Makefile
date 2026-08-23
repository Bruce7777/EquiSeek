.PHONY: bootstrap migrate up down local-init local-api local-worker \
	demo-fake demo-kill-recover demo-sse-reconnect \
	test test-integration test-fault lint typecheck docs-check security release-check sandbox-image \
	desktop-install desktop desktop-smoke desktop-sidecar desktop-build desktop-package

bootstrap:
	uv sync --all-extras --python 3.12
	cp -n .env.example .env || true

migrate:
	uv run alembic upgrade head

up:
	docker compose up --build -d

local-init:
	uv run equiseek init-db

local-api:
	uv run uvicorn aegisrun.api.app:app --host 127.0.0.1 --port 8000

local-worker:
	uv run equiseek worker

down:
	docker compose down

sandbox-image:
	docker build -f Dockerfile.sandbox -t equiseek-sandbox:0.2.0 .

demo-fake:
	uv run equiseek demo-fake --report .equiseek/demo-report.html

demo-kill-recover:
	uv run pytest -q tests/fault_injection/test_kill_recover.py

demo-sse-reconnect:
	uv run pytest -q tests/fault_injection/test_sse_reconnect.py

test:
	uv run pytest -m "not integration" --cov --cov-report=term-missing

test-integration:
	uv run pytest -m integration -q

test-fault:
	uv run pytest tests/fault_injection -q

lint:
	uv run ruff check .

typecheck:
	uv run mypy src

security:
	uv run pip-audit

release-check: lint typecheck test security

desktop-install:
	uv sync --extra desktop-build --extra dev
	npm --prefix apps/desktop ci

desktop:
	EQUISEEK_REPO_ROOT="$(CURDIR)" EQUISEEK_PYTHON="$(CURDIR)/.venv/bin/python" npm --prefix apps/desktop run dev

docs-check:
	python scripts/verify_readme_pair.py

desktop-smoke:
	uv run python -m aegisrun.sidecar --self-test
	npm --prefix apps/desktop run lint
	npm --prefix apps/desktop run typecheck
	npm --prefix apps/desktop run test

desktop-sidecar:
	cd packaging && uv run pyinstaller --noconfirm --clean --distpath ../dist --workpath ../build EquiSeekSidecar.spec

desktop-build: desktop-sidecar
	npm --prefix apps/desktop run package

desktop-package: desktop-sidecar
	npm --prefix apps/desktop run make
