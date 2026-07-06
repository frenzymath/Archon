import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, HashRouter } from 'react-router-dom';
import { BASE_URL } from './utils/constants';
import App from './App';
import { installFetchScope } from './lib/projectScope';
import { installStaticFetch, isStaticDashboard } from './lib/staticMode';
import './styles/global.css';

installStaticFetch();

// Scope every /api/* request to the selected peer project (if any) before the
// app mounts, so all data hooks honour the project switcher with no per-call code.
installFetchScope();

const Router = isStaticDashboard() ? HashRouter : BrowserRouter;
// The live dashboard may sit behind a path-prefix reverse proxy, so the
// BrowserRouter needs the derived base as its basename. The static export
// uses HashRouter (routes live in the fragment) and needs no basename.
const routerBasename = isStaticDashboard()
  ? undefined
  : BASE_URL.replace(/\/+$/, '') || '/';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60,
      retry: 1,
    },
  },
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <Router basename={routerBasename}>

        <App />
      </Router>
    </QueryClientProvider>
  </React.StrictMode>
);
