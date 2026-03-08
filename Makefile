.PHONY: help up down logs build dev shell db-shell psql migrate makemigrations worker beat clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

up: ## Start all Docker containers in the background
	docker-compose up -d

down: ## Stop all Docker containers
	docker-compose down

logs: ## View logs of all Docker containers
	docker-compose logs -f

build: ## Rebuild all Docker images without cache
	docker-compose build --no-cache

dev: ## Run the FastAPI development server locally (requires active python venv)
	uvicorn web.app:app --reload --port 8000

shell: ## Open a bash shell inside the 'app' container
	docker-compose exec app /bin/bash

db-shell: ## Open a psql shell inside the 'postgres' container (assumes default user/db from docker-compose)
	docker-compose exec postgres psql -U spartacus -d spartacus_db

migrate: ## Run Alembic migrations to apply changes to the database
	alembic upgrade head

makemigrations: ## Create a new Alembic migration (usage: make makemigrations msg="your message")
	alembic revision --autogenerate -m "$(msg)"

worker: ## Run the Celery worker locally
	celery -A scheduler.tasks worker --loglevel=info --concurrency=2

beat: ## Run the Celery beat scheduler locally
	celery -A scheduler.tasks beat --loglevel=info

clean: ## Remove Python cache files
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
