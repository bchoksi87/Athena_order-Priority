/**
 * Error envelope of the in-page demo backend. Mirrors backend/app/core/errors.py:
 * every failure becomes `{error, message, details}` with the same status codes the
 * FastAPI application uses (401 unauthenticated, 403 forbidden, 404 not_found,
 * 409 conflict, 422 validation_error).
 */
import type { Role } from "@/api/types";

export class DemoApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.name = "DemoApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  toJSON(): { error: string; message: string; details: Record<string, unknown> } {
    return { error: this.code, message: this.message, details: this.details };
  }
}

export function notFound(message: string, details: Record<string, unknown> = {}): DemoApiError {
  return new DemoApiError(404, "not_found", message, details);
}

export function conflict(message: string, details: Record<string, unknown> = {}): DemoApiError {
  return new DemoApiError(409, "conflict", message, details);
}

export function validation(message: string, details: Record<string, unknown> = {}): DemoApiError {
  return new DemoApiError(422, "validation_error", message, details);
}

export function unauthenticated(message = "missing bearer token"): DemoApiError {
  return new DemoApiError(401, "unauthenticated", message);
}

export function forbiddenWrite(actual: Role, required: Role): DemoApiError {
  return new DemoApiError(403, "forbidden", `role '${actual}' is not allowed; requires '${required}' or higher`, {
    required_role: required,
    actual_role: actual,
  });
}

export function forbiddenRead(actual: Role, required: Role): DemoApiError {
  return new DemoApiError(403, "forbidden", `role '${actual}' may not read this resource; requires '${required}' or higher`, {
    required_role: required,
    actual_role: actual,
  });
}

/** FastAPI-style body validation failure (what a missing `reason` produces on the real API). */
export function requestValidation(errors: Array<{ loc: Array<string | number>; msg: string; type: string }>): DemoApiError {
  return new DemoApiError(422, "validation_error", "request validation failed", { errors });
}

export function missingField(location: "body" | "query", field: string, msg = "Field required"): DemoApiError {
  return requestValidation([{ loc: [location, field], msg, type: "missing" }]);
}

export function invalidField(location: "body" | "query", field: string, msg: string): DemoApiError {
  return requestValidation([{ loc: [location, field], msg, type: "value_error" }]);
}
