import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { BASE_URL } from './utils/constants';
import App from './App';
import { installFetchScope } from './lib/projectScope';
import './styles/global.css';

// Scope every /api/* request to the selected peer project (if any) before the
// app mounts, so all data hooks honour the project switcher with no per-call code.
installFetchScope();

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
      <BrowserRouter basename={BASE_URL.replace(/\/+$/, '') || '/'}>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
