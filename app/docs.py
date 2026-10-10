"""Customised API docs page (/docs).

Swagger UI only shows a schema's description *inside* its collapsible box in
the "Schemas" section. To show a one-line description *above* each box instead:

1. ``custom_openapi`` moves every schema's description into an ``x-summary``
   field (so Swagger no longer shows it inside the box). Descriptions come from
   the class docstrings in ``schemas.py`` / ``models.py``; the three schemas
   FastAPI generates itself get theirs from ``GENERATED_SCHEMA_SUMMARIES``.
2. ``swagger_ui_with_summaries`` serves the normal Swagger page plus a small
   script that inserts each ``x-summary`` above the matching schema box.
"""

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

# Schemas created by FastAPI (not by our classes), so they have no docstring.
GENERATED_SCHEMA_SUMMARIES = {
    "Body_create_job_from_csv_jobs_csv_post": "The form fields and CSV file sent when creating a job from a CSV upload.",
    "HTTPValidationError": "The error response returned when a request's input is invalid (422).",
    "ValidationError": "One specific input problem: which field was wrong and why.",
}

_SUMMARY_SCRIPT = """
<style>
  .schema-summary { margin: 18px 0 4px 4px; color: #3b4151; font-size: 14px; font-family: sans-serif; }
</style>
<script>
(function () {
  var hiddenSchemas = {
    CertificateList: true,
    CertificateStatus: true,
    HTTPValidationError: true,
    JobStatus: true,
    ValidationError: true
  };
  var summaries = null;
  fetch("%(openapi_url)s").then(function (r) { return r.json(); }).then(function (spec) {
    summaries = {};
    var schemas = (spec.components && spec.components.schemas) || {};
    Object.keys(schemas).forEach(function (key) {
      var s = schemas[key];
      if (s["x-summary"]) { summaries[key] = s["x-summary"]; if (s.title) summaries[s.title] = s["x-summary"]; }
    });
    addSummaries();
  });

  function addSummaries() {
    if (!summaries) return;
    // Swagger UI 5 (OpenAPI 3.1) renders top-level schemas as <article>s; older versions use .model-container.
    var boxes = document.querySelectorAll(
      'section.models article[data-json-schema-level="0"], section.models .model-container');
    boxes.forEach(function (box) {
      var titleEl = box.querySelector(".json-schema-2020-12__title");
      var name = titleEl ? titleEl.textContent.trim() : (box.id || "").replace(/^model-/, "");
      if (hiddenSchemas[name]) {
        box.style.display = "none";
        return;
      }
      var prev = box.previousElementSibling;
      if (!summaries[name] || (prev && prev.classList.contains("schema-summary"))) return;
      var p = document.createElement("p");
      p.className = "schema-summary";
      p.textContent = summaries[name];
      box.parentNode.insertBefore(p, box);
    });
  }

  // Swagger renders asynchronously (and re-renders when sections are toggled).
  new MutationObserver(addSummaries).observe(document.body, { childList: true, subtree: true });
})();
</script>
"""


def custom_openapi(app: FastAPI) -> Callable[[], dict[str, Any]]:
    original_openapi = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = original_openapi()
        for name, definition in schema.get("components", {}).get("schemas", {}).items():
            summary = definition.pop("description", None) or GENERATED_SCHEMA_SUMMARIES.get(name)
            if summary:
                definition["x-summary"] = summary
        return schema

    return openapi


def swagger_ui_with_summaries(app: FastAPI) -> HTMLResponse:
    page = get_swagger_ui_html(openapi_url=app.openapi_url, title=f"{app.title} - Swagger UI")
    script = _SUMMARY_SCRIPT % {"openapi_url": app.openapi_url}
    html = page.body.decode().replace("</body>", script + "</body>")
    return HTMLResponse(html)