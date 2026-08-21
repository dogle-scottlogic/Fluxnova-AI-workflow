"""Tests for the local OTLP/HTTP trace receiver."""

import gzip
import json
import threading
import time
from http.client import HTTPConnection
from pathlib import Path

from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
from opentelemetry.proto.common.v1 import common_pb2
from opentelemetry.proto.resource.v1 import resource_pb2
from opentelemetry.proto.trace.v1 import trace_pb2

from fluxnova_listener.otel_receiver import make_server, parse_export_request


def _kv(key: str, **value_kwargs) -> common_pb2.KeyValue:
    return common_pb2.KeyValue(key=key, value=common_pb2.AnyValue(**value_kwargs))


def _sample_export_request() -> trace_service_pb2.ExportTraceServiceRequest:
    span = trace_pb2.Span(
        trace_id=bytes.fromhex("11" * 16),
        span_id=bytes.fromhex("22" * 8),
        name="invoke_agent {AdHocSubProcess_LoanAssessmentAgent}",
        start_time_unix_nano=0,
        end_time_unix_nano=1_000_000_000,
        attributes=[
            _kv("gen_ai.operation.name", string_value="invoke_agent"),
            _kv("gen_ai.conversation.id", string_value="proc-123"),
            _kv("gen_ai.invoke_agent.inference_calls", int_value=3),
        ],
        status=trace_pb2.Status(code=trace_pb2.Status.STATUS_CODE_OK),
    )
    scope_spans = trace_pb2.ScopeSpans(spans=[span])
    resource_spans = trace_pb2.ResourceSpans(
        resource=resource_pb2.Resource(
            attributes=[_kv("service.name", string_value="fluxnova-agentic-subprocess-local")]
        ),
        scope_spans=[scope_spans],
    )
    return trace_service_pb2.ExportTraceServiceRequest(resource_spans=[resource_spans])


class TestParseExportRequest:
    def test_extracts_span_with_flattened_attributes(self):
        request = _sample_export_request()
        spans = parse_export_request(request.SerializeToString())

        assert len(spans) == 1
        span = spans[0]
        assert span["trace_id"] == "11" * 16
        assert span["span_id"] == "22" * 8
        assert span["attributes"]["gen_ai.operation.name"] == "invoke_agent"
        assert span["attributes"]["gen_ai.conversation.id"] == "proc-123"
        assert span["attributes"]["gen_ai.invoke_agent.inference_calls"] == 3
        assert span["resource_attributes"]["service.name"] == "fluxnova-agentic-subprocess-local"
        assert span["status_code"] == "OK"
        assert span["end_time_unix_nano"] == 1_000_000_000


class TestReceiverServer:
    def test_receives_and_persists_spans_over_http(self, tmp_path: Path):
        store_path = tmp_path / "spans.jsonl"
        server = make_server(store_path, port=0)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            body = _sample_export_request().SerializeToString()
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "POST",
                "/v1/traces",
                body=body,
                headers={"Content-Type": "application/x-protobuf"},
            )
            response = conn.getresponse()
            assert response.status == 200
            response.read()
            conn.close()

            # give the handler's file write a beat, then check the store.
            for _ in range(20):
                if store_path.exists() and store_path.read_text().strip():
                    break
                time.sleep(0.05)
        finally:
            server.shutdown()
            server.server_close()

        lines = store_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        stored = json.loads(lines[0])
        assert stored["attributes"]["gen_ai.conversation.id"] == "proc-123"

    def test_accepts_gzip_encoded_body(self, tmp_path: Path):
        store_path = tmp_path / "spans.jsonl"
        server = make_server(store_path, port=0)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            body = gzip.compress(_sample_export_request().SerializeToString())
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request(
                "POST",
                "/v1/traces",
                body=body,
                headers={
                    "Content-Type": "application/x-protobuf",
                    "Content-Encoding": "gzip",
                },
            )
            response = conn.getresponse()
            assert response.status == 200
            response.read()
            conn.close()

            for _ in range(20):
                if store_path.exists() and store_path.read_text().strip():
                    break
                time.sleep(0.05)
        finally:
            server.shutdown()
            server.server_close()

        assert store_path.exists()
        lines = store_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1

    def test_unknown_path_returns_404(self, tmp_path: Path):
        store_path = tmp_path / "spans.jsonl"
        server = make_server(store_path, port=0)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("POST", "/not-traces", body=b"")
            response = conn.getresponse()
            assert response.status == 404
            response.read()
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
