---
description: 'Required conventions for dataviewer backend and frontend development'
applyTo: 'src/dataviewer/**'
---

# Dataviewer Development Instructions

Conventions and patterns for the Dataset Analysis Tool codebase at `src/dataviewer/`.

## Stack

| Layer | Technology | Directory |
|-------|-----------|-----------|
| Backend | FastAPI, Pydantic, dotenv | `backend/src/api/` |
| Frontend | React 18, Vite, TypeScript, Tailwind CSS | `frontend/src/` |
| State | Zustand (stores), TanStack React Query (server state) | `frontend/src/stores/`, `frontend/src/hooks/` |
| UI | shadcn/ui components | `frontend/src/components/ui/` |

## Backend Conventions

- Add REST endpoints as routers in `backend/src/api/routers/`.
- Register new routers in `backend/src/api/main.py` with a prefix and tag.
- Define request/response models in `backend/src/api/models/`.
- Business logic goes in `backend/src/api/services/`.
- Use `Depends()` for service injection via `get_dataset_service`.
- Relative imports within the `api` package.
- Environment config loaded from `backend/.env` via `dotenv`.
- `HMI_DATA_PATH` is resolved to absolute at app startup in `main.py`.

## Frontend Conventions

- Components are organized by feature directory under `frontend/src/components/`.
- API calls use the typed `ApiClient` in `frontend/src/api/client.ts`.
- Server state uses TanStack React Query hooks in `frontend/src/hooks/`.
- Client state uses Zustand stores in `frontend/src/stores/`.
- Type definitions live in `frontend/src/types/` and re-export from `index.ts`.
- Path alias `@/` maps to `frontend/src/`.
- CORS: backend allows frontend on ports 5173-5177.

## Testing

- Backend: `cd backend && pytest`
- Frontend: `cd frontend && npm run lint && npm run build`
- Backend lint: `cd backend && ruff check src/`

## App Lifecycle

- `start.sh` orchestrates both services with health checking and graceful shutdown.
- Backend auto-reloads on Python file changes (`--reload`).
- Frontend uses Vite HMR for instant updates.
- Stop with `Ctrl+C` (sends SIGINT, triggers cleanup trap).
