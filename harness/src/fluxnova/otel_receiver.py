"""Minimal local OTLP/HTTP trace receiver.

Accepts ``POST /v1/traces`` (protobuf, optional gzip) and appends each span
as one JSON line to a local store file, so ``OtelClient`` can query them
without depending on a vendor trace-store backend. See
docs/deepeval-otel-gap-analysis.md and GENAI_SEMCONV_ALIGNMENT.md ("Harness
OTLP receiver") for the collector config and rationale.

Run standalone: ``otel-receiver --port 4319 --store harness/.fluxnova/otel-spans.json``
"""

from __future__ import annotations

import argparse
import gzip
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span as PbSpan
from opentelemetry.proto.trace.v1.trace_pb2 import Status as PbStatus

_STATUS_CODE_NAMES = {
    PbStatus.STATUS_CODE_UNSET: "UNSET",
    PbStatus.STATUS_CODE_OK: "OK",
    PbStatus.STATUS_CODE_ERROR: "ERROR",
}

_DEFAULT_STORE = Path("harness/.fluxnova/otel-spans.json")
_DEFAULT_PORT = 4319


def _any_value_to_python(value: AnyValue) -> Any:
    kind = value.WhichOneof("value")
    if kind is None:
        return None
    if kind == "array_value":
        return [_any_value_to_python(v) for v in value.array_value.values]
    if kind == "kvlist_value":
        return _kv_list_to_dict(value.kvlist_value.values)
    return getattr(value, kind)


def _kv_list_to_dict(kvs: list[KeyValue]) -> dict[str, Any]:
    return {kv.key: _any_value_to_python(kv.value) for kv in kvs}


def span_to_dict(span: PbSpan, trace_id_hex: str, resource_attrs: dict[str, Any]) -> dict[str, Any]:
    """Flatten one OTLP protobuf ``Span`` into the JSON shape stored on disk."""
    return {
        "trace_id": trace_id_hex,
        "span_id": span.span_id.hex(),
        "parent_span_id": span.parent_span_id.hex() or None,
        "name": span.name,
        "start_time_unix_nano": span.start_time_unix_nano,
        "end_time_unix_nano": span.end_time_unix_nano,
        "attributes": _kv_list_to_dict(span.attributes),
        "resource_attributes": resource_attrs,
        "status_code": _STATUS_CODE_NAMES.get(span.status.code, "UNSET"),
        "status_message": span.status.message or None,
    }


def parse_export_request(body: bytes) -> list[dict[str, Any]]:
    """Parse an OTLP/HTTP ``ExportTraceServiceRequest`` protobuf body into span dicts."""
    request = trace_service_pb2.ExportTraceServiceRequest()
    request.ParseFromString(body)
    spans: list[dict[str, Any]] = []
    for resource_spans in request.resource_spans:
        resource_attrs = _kv_list_to_dict(resource_spans.resource.attributes)
        for scope_spans in resource_spans.scope_spans:
            for span in scope_spans.spans:
                spans.append(span_to_dict(span, span.trace_id.hex(), resource_attrs))
    return spans


class _TraceReceiverHandler(BaseHTTPRequestHandler):
    store_path: Path = _DEFAULT_STORE
    _lock = threading.Lock()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        pass  # keep test/CLI output quiet; failures still surface via response codes

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler naming
        if self.path.rstrip("/") != "/v1/traces":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if self.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)

        try:
            spans = parse_export_request(body)
        except Exception:
            self.send_response(400)
            self.end_headers()
            return

        if spans:
            self._append_spans(spans)

        response = trace_service_pb2.ExportTraceServiceResponse()
        payload = response.SerializeToString()
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _append_spans(self, spans: list[dict[str, Any]]) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.store_path.open("a", encoding="utf-8") as fh:
            for span in spans:
                fh.write(json.dumps(span) + "\n")


def make_server(store_path: Path, port: int) -> ThreadingHTTPServer:
    """Build a (not-yet-started) receiver server writing spans to ``store_path``."""
    handler_cls = type(
        "BoundTraceReceiverHandler",
        (_TraceReceiverHandler,),
        {"store_path": store_path},
    )
    return ThreadingHTTPServer(("0.0.0.0", port), handler_cls)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minimal local OTLP/HTTP trace receiver")
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    parser.add_argument("--store", type=Path, default=_DEFAULT_STORE)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    server = make_server(args.store, args.port)
    print(f"OTLP trace receiver listening on :{args.port} (POST /v1/traces) — writing to {args.store}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
