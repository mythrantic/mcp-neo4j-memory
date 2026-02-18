.PHONY: help dev install

# Colors for output
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
NC := \033[0m # No Color

# Local development URLs (override production URLs in .env files)
VIS_DOMAIN := http://localhost:3000

help: ## Show this help message
	@echo "$(BLUE)MCP Development Commands$(NC)"
	@echo "Local VIS domain: $(VIS_DOMAIN)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "$(GREEN)%-20s$(NC) %s\n", $$1, $$2}'

# Development commands
dev: ## Start all services in development mode (requires tmux or multiple terminals)
	@echo "$(YELLOW)Starting all services...$(NC)"
	@$(MAKE) -j1 dev-mcp-neo4j-memory

dev-mcp-neo4j-memory: ## Start ANTVIS in-memory Neo4j server (port 7687)start the container
	@echo "$(BLUE)Starting ANTVIS in-memory Neo4j server on bolt://localhost:7687$(NC)"
	@uv run python src/mcp_neo4j_memory/server.py

install: ## Install all (uv)
	@echo "$(BLUE)Installing dependencies...$(NC)"
	@$(MAKE) -j1 install-mcp-neo4j-memory

install-mcp-neo4j-memory: ## Install ANTVIS in-memory Neo4j server dependencies (uv)
	@echo "$(BLUE)Installing ANTVIS in-memory Neo4j server dependencies...$(NC)"
	@uv sync

clean: ## Clean all build artifacts and dependencies
	@echo "$(BLUE)Cleaning all build artifacts and dependencies...$(NC)"
	@$(MAKE) -j1 clean-mcp-neo4j-memory

clean-mcp-neo4j-memory: ## Clean ANTVIS in-memory Neo4j server build artifacts and dependencies
	@echo "$(BLUE)Cleaning ANTVIS in-memory Neo4j server...$(NC)"
	@rm -rf .venv __pycache__
	
# Default target
.DEFAULT_GOAL := help