#!/bin/bash
echo
echo "=== Running containers ==="
docker compose ps --format "table {{.Service}}\t{{.Status}}\t{{.Ports}}"
echo
echo "=== Links (Ctrl+click in the VS Code terminal to open) ==="
echo "App:        http://localhost:3000"
echo "Library:    http://localhost:3000/library"
echo "Ask:        http://localhost:3000/chat"
echo "Compare:    http://localhost:8000/compare" | sed 's/8000/3000/'
echo "API docs:   http://localhost:8000/docs"
echo "API health: http://localhost:8000/health"
echo
