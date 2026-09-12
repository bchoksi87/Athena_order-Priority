"""Generate docs/API.md from the live FastAPI application (routes, summaries, role requirements).

Usage: cd backend && .venv/bin/python scripts/generate_api_docs.py > ../docs/API.md
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi.routing import APIRoute

from app.domain.enums import Role
from app.main import create_app


def _role_from_dependency(call: Any) -> tuple[str, Role] | None:
    closure = getattr(call, "__closure__", None) or ()
    qualname = getattr(call, "__qualname__", "")
    for cell in closure:
        try:
            value = cell.cell_contents
        except ValueError:  # empty cell
            continue
        if isinstance(value, Role):
            kind = "read" if "require_read_access" in qualname else "min"
            return kind, value
    return None


def _access(dependant: Any) -> str:
    found: list[tuple[str, Role]] = []
    stack = list(dependant.dependencies)
    seen: set[int] = set()
    while stack:
        dep = stack.pop()
        if id(dep) in seen:
            continue
        seen.add(id(dep))
        info = _role_from_dependency(dep.call)
        if info:
            found.append(info)
        stack.extend(dep.dependencies)
    if not found:
        if any(getattr(d.call, "__name__", "") == "get_current_user" for d in dependant.dependencies):
            return "any authenticated user"
        return "public"
    kind, role = found[0]
    return f"{role.value}+ or executive (read)" if kind == "read" else f"{role.value}+"


def _iter_routes(routes: Any) -> Any:
    """Yield (path, methods, summary, tags, dependant) across plain and lazily included routers."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route.path, route.methods, route.summary or route.name, route.tags, route.dependant
        elif hasattr(route, "effective_route_contexts"):
            for ctx in route.effective_route_contexts():
                yield ctx.path, ctx.methods, ctx.summary or ctx.name, ctx.tags, ctx.dependant


def main() -> None:
    app = create_app()
    schema = app.openapi()
    by_tag: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    for path, methods, summary, tags, dependant in _iter_routes(app.routes):
        if not path.startswith("/api/"):
            continue
        for method in sorted(set(methods) - {"HEAD", "OPTIONS"}):
            op = schema["paths"].get(path, {}).get(method.lower(), {})
            tag = (op.get("tags") or list(tags) or ["misc"])[0]
            by_tag[str(tag)].append((method, path, op.get("summary") or summary, _access(dependant)))
    print("# API reference\n")
    print(
        "Generated from the FastAPI application (`backend/scripts/generate_api_docs.py`). "
        "All endpoints are prefixed with `/api/v1`, exchange JSON, and require a bearer JWT obtained "
        "from `POST /auth/login` unless marked public. Errors use the envelope "
        '`{"error": <code>, "message": <text>, "details": {...}}`. Role precedence: '
        "executive < operator < supervisor < planner < production_manager < admin; `X+` means role X "
        "or higher, and read endpoints additionally admit the read-only executive role. "
        "The interactive OpenAPI UI is served at `/docs`.\n"
    )
    for tag in sorted(by_tag):
        print(f"## {tag}\n")
        print("| Method | Path | Summary | Access |")
        print("|---|---|---|---|")
        for method, path, summary, access in sorted(by_tag[tag], key=lambda r: (r[1], r[0])):
            print(f"| `{method}` | `{path}` | {summary} | {access} |")
        print()
    print("## Conventions\n")
    print(
        "* Paginated list endpoints accept `page` and `page_size` and return "
        "`{items, total, page, page_size, pages, has_more}`.\n"
        "* Every mutating endpoint that changes priorities, locks, expedites, configuration or plan "
        "status requires a `reason` and writes an audit row (`GET /audit`).\n"
        "* Timestamps are ISO-8601 UTC; durations are minutes unless the field name says hours.\n"
        "* `POST /schedule/generate` creates a DRAFT version; `approve` and `publish` move it through "
        "the state machine (draft → approved → published → superseded). In READ_ONLY writeback mode "
        "publishing activates the plan inside PPSE and records a skipped writeback receipt.\n"
    )


if __name__ == "__main__":
    main()
