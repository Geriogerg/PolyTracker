import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { LiveProvider } from "./lib/live";
import AlertsPage from "./pages/Alerts";
import ClusterPage from "./pages/Cluster";
import ClustersPage from "./pages/Clusters";
import MarketPage from "./pages/Market";
import MarketsPage from "./pages/Markets";
import OverviewPage from "./pages/Overview";
import SettingsPage from "./pages/Settings";
import StatusPage from "./pages/Status";
import WalletPage from "./pages/Wallet";
import WalletsPage from "./pages/Wallets";
import "./styles.css";

try {
  document.documentElement.dataset.theme = localStorage.getItem("pt-theme") ?? "dark";
} catch {
  document.documentElement.dataset.theme = "dark";
}

const qc = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } } });

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <LiveProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<OverviewPage />} />
              <Route path="clusters" element={<ClustersPage />} />
              <Route path="clusters/:key" element={<ClusterPage />} />
              <Route path="wallets" element={<WalletsPage />} />
              <Route path="wallets/:address" element={<WalletPage />} />
              <Route path="markets" element={<MarketsPage />} />
              <Route path="markets/:cid" element={<MarketPage />} />
              <Route path="alerts" element={<AlertsPage />} />
              <Route path="settings" element={<SettingsPage />} />
              <Route path="status" element={<StatusPage />} />
              <Route path="*" element={<div className="empty"><h3>Page not found</h3></div>} />
            </Route>
          </Routes>
        </BrowserRouter>
      </LiveProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);
