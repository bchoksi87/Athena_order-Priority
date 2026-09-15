import { lazy, Suspense, type ReactNode } from "react";
import { createBrowserRouter, createHashRouter, Navigate, RouterProvider } from "react-router-dom";

import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { isDemoMode } from "@/demo";

import { AppShell } from "./AppShell";
import { useAuth } from "./auth";
import { homeForRole, navGroups, routes } from "./nav";
import { RequireAuth, RequireRole } from "./RouteGuard";

const LoginPage = lazy(() => import("@/pages/Login/LoginPage"));
const ExecutiveDashboardPage = lazy(() => import("@/pages/ExecutiveDashboard/ExecutiveDashboardPage"));
const ControlTowerPage = lazy(() => import("@/pages/ControlTower/ControlTowerPage"));
const PriorityQueuePage = lazy(() => import("@/pages/PriorityQueue/PriorityQueuePage"));
const MachineSchedulePage = lazy(() => import("@/pages/MachineSchedule/MachineSchedulePage"));
const GanttSchedulePage = lazy(() => import("@/pages/GanttSchedule/GanttSchedulePage"));
const OrderDetailPage = lazy(() => import("@/pages/OrderDetail/OrderDetailPage"));
const MachineDetailPage = lazy(() => import("@/pages/MachineDetail/MachineDetailPage"));
const BottleneckAnalysisPage = lazy(() => import("@/pages/BottleneckAnalysis/BottleneckAnalysisPage"));
const CapacityPlanningPage = lazy(() => import("@/pages/CapacityPlanning/CapacityPlanningPage"));
const WhatIfSimulationPage = lazy(() => import("@/pages/WhatIfSimulation/WhatIfSimulationPage"));
const PriorityConfigurationPage = lazy(() => import("@/pages/PriorityConfiguration/PriorityConfigurationPage"));
const SchedulingConfigurationPage = lazy(() => import("@/pages/SchedulingConfiguration/SchedulingConfigurationPage"));
const AlertsPage = lazy(() => import("@/pages/Alerts/AlertsPage"));
const DataQualityPage = lazy(() => import("@/pages/DataQuality/DataQualityPage"));
const AuditLogPage = lazy(() => import("@/pages/AuditLog/AuditLogPage"));
const SystemAdministrationPage = lazy(() => import("@/pages/SystemAdministration/SystemAdministrationPage"));

const navByPath = new Map(navGroups.flatMap((g) => g.items).map((item) => [item.path, item]));

function guarded(path: string, element: ReactNode): ReactNode {
  const item = navByPath.get(path);
  if (!item) return element;
  return (
    <RequireRole minRole={item.minRole} readOnly={item.readOnly}>
      {element}
    </RequireRole>
  );
}

function HomeRedirect() {
  const { user } = useAuth();
  return <Navigate to={user ? homeForRole(user.role) : routes.login} replace />;
}

function lazyPage(node: ReactNode): ReactNode {
  return <Suspense fallback={<LoadingState label="Loading screen" />}>{node}</Suspense>;
}

// The static demo bundle is served from any path (or embedded in a host page), so deep links and
// refreshes only work with hash routing; the real deployment keeps clean URLs (nginx falls back to index.html).
const createRouter = isDemoMode() ? createHashRouter : createBrowserRouter;

export const router = createRouter([
  { path: routes.login, element: lazyPage(<LoginPage />) },
  {
    path: "/",
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    errorElement: <ErrorState title="Something went wrong" message="The screen failed to render. Reload to try again." />,
    children: [
      { index: true, element: <HomeRedirect /> },
      { path: routes.executive, element: lazyPage(guarded(routes.executive, <ExecutiveDashboardPage />)) },
      { path: routes.controlTower, element: lazyPage(guarded(routes.controlTower, <ControlTowerPage />)) },
      { path: routes.priorityQueue, element: lazyPage(guarded(routes.priorityQueue, <PriorityQueuePage />)) },
      { path: routes.machineSchedule, element: lazyPage(guarded(routes.machineSchedule, <MachineSchedulePage />)) },
      { path: routes.gantt, element: lazyPage(guarded(routes.gantt, <GanttSchedulePage />)) },
      {
        path: routes.orderDetail(),
        element: lazyPage(
          <RequireRole minRole="operator" readOnly>
            <OrderDetailPage />
          </RequireRole>,
        ),
      },
      {
        path: routes.machineDetail(),
        element: lazyPage(
          <RequireRole minRole="operator" readOnly>
            <MachineDetailPage />
          </RequireRole>,
        ),
      },
      { path: routes.bottlenecks, element: lazyPage(guarded(routes.bottlenecks, <BottleneckAnalysisPage />)) },
      { path: routes.capacity, element: lazyPage(guarded(routes.capacity, <CapacityPlanningPage />)) },
      { path: routes.simulation, element: lazyPage(guarded(routes.simulation, <WhatIfSimulationPage />)) },
      { path: routes.priorityConfig, element: lazyPage(guarded(routes.priorityConfig, <PriorityConfigurationPage />)) },
      {
        path: routes.schedulingConfig,
        element: lazyPage(guarded(routes.schedulingConfig, <SchedulingConfigurationPage />)),
      },
      { path: routes.alerts, element: lazyPage(guarded(routes.alerts, <AlertsPage />)) },
      { path: routes.dataQuality, element: lazyPage(guarded(routes.dataQuality, <DataQualityPage />)) },
      { path: routes.audit, element: lazyPage(guarded(routes.audit, <AuditLogPage />)) },
      { path: routes.admin, element: lazyPage(guarded(routes.admin, <SystemAdministrationPage />)) },
      { path: "*", element: <ErrorState title="Not found" message="No screen matches this address." /> },
    ],
  },
]);

export function AppRouter() {
  return <RouterProvider router={router} />;
}
