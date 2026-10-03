import { BrowserRouter, Link, Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { PageHeader } from "./components/ui";
import { ApprovalsPage } from "./pages/ApprovalsPage";
import { AuditPage } from "./pages/AuditPage";
import { ContextPage } from "./pages/ContextPage";
import { DiscoveryPage } from "./pages/DiscoveryPage";
import { OverviewPage } from "./pages/OverviewPage";
import { RecommendationsPage } from "./pages/RecommendationsPage";
import { SystemPage } from "./pages/SystemPage";
import { WorkflowDetailPage } from "./pages/WorkflowDetailPage";
import { WorkflowsPage } from "./pages/WorkflowsPage";

function NotFound() {
  return (
    <>
      <PageHeader title="Page not found" />
      <p>
        <Link to="/">Back to the overview</Link>
      </p>
    </>
  );
}

/** The routes without a router, so tests can supply a MemoryRouter. */
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<OverviewPage />} />
        <Route path="workflows" element={<WorkflowsPage />} />
        <Route path="workflows/:threadId" element={<WorkflowDetailPage />} />
        <Route path="approvals" element={<ApprovalsPage />} />
        <Route path="discovery" element={<DiscoveryPage />} />
        <Route path="recommendations" element={<RecommendationsPage />} />
        <Route path="audit" element={<AuditPage />} />
        <Route path="context" element={<ContextPage />} />
        <Route path="system" element={<SystemPage />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  );
}
