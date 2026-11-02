"""FastAPI application for ``amber ui`` (``docs/07-panel.md``). Binds 127.0.0.1, no auth."""

from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import PythonLexer

from amber.errors import AmberError
from amber.ui.charts import guard_events_svg, tokens_per_run_svg
from amber.ui.data import Panel
from amber.util.json import dumps_canonical

HERE = Path(__file__).parent
STATIC = HERE / "static"
TEMPLATES = HERE / "templates"


def _json(payload: Any) -> Response:
    return Response(dumps_canonical(payload), media_type="application/json")


def create_app(root: Path, *, panel: Panel | None = None) -> FastAPI:
    panel = panel or Panel(root)
    app = FastAPI(title="Amber panel", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES))
    formatter = HtmlFormatter(cssclass="highlight", nowrap=False)

    def page(request: Request, name: str, active: str, **ctx: Any) -> HTMLResponse:
        return templates.TemplateResponse(
            request, name, {"active": active, "workflows": panel.workflows(), **ctx}
        )

    # --- pages -------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request) -> HTMLResponse:
        rows = panel.overview()
        token_rows, baseline = panel.tokens_chart_rows()
        guard_rows = panel.guard_chart_rows()
        runs = panel.runs(n=500)
        tiles = {
            "runs": len(runs),
            "llm_tokens": sum(r.llm_input_tokens + r.llm_output_tokens for r in runs),
            "tokens_saved": sum(r.tokens_saved for r in rows),
            "cost_avoided": None,
        }
        return page(
            request, "overview.html", "overview", rows=rows, tiles=tiles,
            tokens_svg=tokens_per_run_svg(token_rows, baseline=baseline), token_rows=token_rows,
            baseline=baseline, guard_svg=guard_events_svg(guard_rows), guard_rows=guard_rows,
        )  # fmt: skip

    @app.get("/programs", response_class=HTMLResponse)
    def programs(request: Request) -> HTMLResponse:
        return page(request, "programs.html", "programs", rows=panel.programs(), workflow=None)

    @app.get("/programs/{workflow}", response_class=HTMLResponse)
    def program(request: Request, workflow: str) -> HTMLResponse:
        rows = panel.programs(workflow)
        if not rows:
            raise HTTPException(404, f"no programs for {workflow}")
        return page(request, "programs.html", "programs", rows=rows, workflow=workflow)

    @app.get("/programs/{workflow}/v{version}", response_class=HTMLResponse)
    def version(request: Request, workflow: str, version: int) -> HTMLResponse:
        try:
            info, data = panel.version(workflow, version)
        except AmberError as e:
            raise HTTPException(404, e.message) from e
        source_html = highlight(data["source"], PythonLexer(), formatter)
        return page(
            request, "version.html", "programs", workflow=workflow, version=version, info=info,
            manifest=data["manifest"], report=data["report"], source_html=source_html,
            pygments_css=formatter.get_style_defs(".highlight"),
        )  # fmt: skip

    @app.get("/runs", response_class=HTMLResponse)
    def runs(request: Request, workflow: str | None = None) -> HTMLResponse:
        token_rows, baseline = panel.tokens_chart_rows(workflow)
        return page(
            request, "runs.html", "runs", rows=panel.runs(workflow), workflow=workflow,
            tokens_svg=tokens_per_run_svg(token_rows, baseline=baseline), token_rows=token_rows,
            baseline=baseline,
        )  # fmt: skip

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run(request: Request, run_id: str) -> HTMLResponse:
        detail = panel.run(run_id)
        if detail is None:
            raise HTTPException(404, f"no run {run_id}")
        return page(request, "run.html", "runs", detail=detail)

    @app.get("/doctor/{workflow}", response_class=HTMLResponse)
    def doctor(request: Request, workflow: str) -> HTMLResponse:
        return page(
            request,
            "doctor.html",
            "doctor",
            workflow=workflow,
            diagnosis=panel.diagnoses.get(workflow),
        )

    @app.post("/doctor/{workflow}", response_class=HTMLResponse)
    def doctor_run(request: Request, workflow: str, probe: int = 1) -> HTMLResponse:
        try:
            diagnosis = panel.run_doctor(workflow, probe=bool(probe))
        except AmberError as e:
            return templates.TemplateResponse(
                request, "_doctor_result.html", {"diagnosis": None, "error": e.message}
            )
        return templates.TemplateResponse(
            request, "_doctor_result.html", {"diagnosis": diagnosis, "error": None}
        )

    @app.get("/decisions", response_class=HTMLResponse)
    def decisions(request: Request, workflow: str | None = None) -> HTMLResponse:
        return page(
            request,
            "decisions.html",
            "decisions",
            rows=panel.decisions(workflow),
            workflow=workflow,
        )

    @app.get("/traces/{trace_id}", response_class=HTMLResponse)
    def trace(request: Request, trace_id: str) -> HTMLResponse:
        try:
            data = panel.trace(trace_id)
        except AmberError as e:
            raise HTTPException(404, e.message) from e
        return page(request, "trace.html", "programs", trace=data)

    # --- JSON twins --------------------------------------------------------
    @app.get("/api/overview")
    def api_overview() -> Response:
        return _json([r.model_dump(mode="json") for r in panel.overview()])

    @app.get("/api/programs")
    def api_programs(workflow: str | None = None) -> Response:
        return _json([r.model_dump(mode="json") for r in panel.programs(workflow)])

    @app.get("/api/runs")
    def api_runs(workflow: str | None = None) -> Response:
        return _json([r.model_dump(mode="json") for r in panel.runs(workflow)])

    @app.get("/api/runs/{run_id}")
    def api_run(run_id: str) -> Response:
        detail = panel.run(run_id)
        if detail is None:
            return JSONResponse({"error": f"no run {run_id}"}, status_code=404)
        return _json(detail.model_dump(mode="json"))

    @app.get("/api/decisions")
    def api_decisions(workflow: str | None = None) -> Response:
        return _json([r.model_dump(mode="json") for r in panel.decisions(workflow)])

    @app.get("/api/doctor/{workflow}")
    def api_doctor(workflow: str) -> Response:
        d = panel.diagnoses.get(workflow)
        return _json(d.to_json() if d else None)

    @app.get("/api/traces/{trace_id}")
    def api_trace(trace_id: str) -> Response:
        try:
            return _json(panel.trace(trace_id))
        except AmberError as e:
            return JSONResponse({"error": e.message}, status_code=404)

    return app


def run_ui(root: Path, *, port: int = 8787, open_browser: bool = False) -> None:
    import uvicorn

    url = f"http://127.0.0.1:{port}/"
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(create_app(root), host="127.0.0.1", port=port, log_level="warning")
