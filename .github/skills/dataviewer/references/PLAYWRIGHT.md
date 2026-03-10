# Playwright Interaction Reference

Selector patterns and interaction recipes for the Dataset Analysis Tool UI at `http://localhost:5173`.

## Page Structure

```text
┌─────────────────────────────────────────────────────┐
│ header                                              │
│   h1: "Robotic Training Data Analysis"              │
│   Dataset selector: <select> or <input>             │
├──────────┬──────────────────────────────────────────┤
│ aside    │ main                                     │
│ (264px)  │                                          │
│          │ AnnotationWorkspace                      │
│ Label    │   Frame viewer                           │
│ Filter   │   Annotation panel                       │
│          │   Timeline                               │
│ Episode  │   Controls                               │
│ List     │                                          │
│          │                                          │
└──────────┴──────────────────────────────────────────┘
```

## Selector Reference

### Header Area

| Element | Selector | Notes |
|---------|----------|-------|
| App title | `header h1` | Text: "Robotic Training Data Analysis" |
| Dataset dropdown | `header select` | Present when multiple datasets loaded |
| Dataset text input | `header input[type="text"]` | Present when single/no datasets |

### Episode Sidebar

| Element | Selector | Notes |
|---------|----------|-------|
| Sidebar container | `aside` | Fixed 256px width (`w-64`) |
| Episode count label | `aside .text-sm.font-medium` | Shows "N Episodes" |
| Episode list | `aside ul` | Scrollable list |
| Episode item | `aside li button` | Click to select; active has `bg-accent` class |
| Episode index text | `aside li button .font-medium` | Text: "Episode N" |
| Episode metadata | `aside li button .text-sm` | Text: "N frames • Task N" |
| Annotated badge | `aside li button .text-green-600` | Text: "✓ Annotated" |
| Episode label tags | `aside li button .rounded-full` | Label chips |

### Main Workspace

| Element | Selector | Notes |
|---------|----------|-------|
| Main content | `main` | Full annotation workspace |
| Loading state | `main .text-muted-foreground:has-text("Loading")` | During data fetch |
| Error state | `main .text-red-500` | On fetch error |

## Common Interaction Recipes

### Browse Datasets

```text
1. Navigate to http://localhost:5173
2. Snapshot to see current state
3. If multiple datasets: click "header select" → select option by value
4. If single dataset: the input shows the current dataset ID
```

### Select an Episode

```text
1. Snapshot the sidebar to see available episodes
2. Click the episode button: aside li:nth-child(N) button
3. Wait for main content to update
4. Take screenshot to verify
```

### Switch Dataset

```text
1. Click the dataset selector in the header
2. Select the target dataset option
3. Wait for episode list to reload
4. First episode auto-selects
```

### Check for Errors

```text
1. Use browser_console_messages to check for JS errors
2. Use browser_network_requests to check for failed API calls
3. Check terminal output for backend errors
```

### Verify API Health

```text
1. Navigate to http://localhost:8000/health
2. Expect JSON: {"status": "healthy"}
3. Navigate to http://localhost:8000/docs for Swagger UI
```

## API Endpoints for Direct Testing

Use Playwright's `browser_evaluate` or `browser_navigate` for direct API inspection:

| Endpoint | Purpose |
|----------|---------|
| `http://localhost:8000/health` | Health check |
| `http://localhost:8000/api/datasets` | List all datasets (JSON array) |
| `http://localhost:8000/api/datasets/{id}/episodes?limit=10` | List episodes |
| `http://localhost:8000/docs` | Interactive API documentation |

## Keyboard Shortcuts

The app registers keyboard shortcuts via the `useKeyboardShortcuts` hook. Check `src/dataviewer/frontend/src/hooks/use-keyboard-shortcuts.ts` for current bindings.

## State Architecture

| Store | Location | Purpose |
|-------|----------|---------|
| `useEpisodeStore` | `stores/` | Current episode, frame index, playback |
| `useDatasetStore` | `stores/` | Selected dataset, dataset list |
| `useLabelStore` | `stores/label-store` | Episode labels, filter state |

Query hooks in `hooks/` manage server-state via TanStack React Query with the `queryClient` from `lib/query-client`.
