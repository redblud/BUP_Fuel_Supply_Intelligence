# FuelOps web

React operator console. Reads only `/api/*` (proxied to the backend on :8000).

```bash
npm ci
npm run dev
npm run gen:api   # after the backend contract changes (make contracts)
```

Never edit `src/api/generated/` by hand.
