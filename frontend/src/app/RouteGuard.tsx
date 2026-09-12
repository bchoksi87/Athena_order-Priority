import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import type { Role } from "@/api/types";
import { LoadingState } from "@/components/LoadingState";
import { ErrorState } from "@/components/ErrorState";
import { ROLE_LABELS } from "@/lib/constants";

import { useAuth } from "./auth";
import { routes } from "./nav";

interface RequireAuthProps {
  children: ReactNode;
}

/** Redirects anonymous visitors to /login, remembering where they wanted to go. */
export function RequireAuth({ children }: RequireAuthProps) {
  const { status } = useAuth();
  const location = useLocation();
  if (status === "loading") return <LoadingState label="Restoring session" fullPage />;
  if (status === "anonymous") return <Navigate to={routes.login} replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

interface RequireRoleProps {
  minRole: Role;
  /** When true the read-only executive role is also admitted (contract §9). */
  readOnly?: boolean;
  children: ReactNode;
}

/** Renders children only when the current user satisfies the role requirement. */
export function RequireRole({ minRole, readOnly = false, children }: RequireRoleProps) {
  const { user, hasMinRole, hasReadAccess } = useAuth();
  const allowed = readOnly ? hasReadAccess(minRole) : hasMinRole(minRole);
  if (!allowed) {
    return (
      <ErrorState
        title="Not authorised"
        message={`This screen requires the ${ROLE_LABELS[minRole]} role or higher. You are signed in as ${
          user ? ROLE_LABELS[user.role] : "an unknown role"
        }.`}
      />
    );
  }
  return <>{children}</>;
}
